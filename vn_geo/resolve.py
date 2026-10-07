"""vn_geo.resolve — non-destructive cross-source entity resolution (R14-C).

Contract: ``analysis/r14-interfaces.md`` §3 (FROZEN). Entities are
``format="entity"`` documents in a SearchStore database (entity schema v1
meta, exposed through the ``entities`` view — see ``searchstore.store``).
v1 is non-destructive: matches are emitted as ``entity_aliased`` events via
``SearchStore.record_event``; documents are never rewritten or deleted and
consumers pick the canonical record at query time.

Matching rules (deterministic, priority order — first hit wins):

  (a) ``tax_code`` exact after digits-only normalization — strongest;
  (b) folded-name exact equality (``vn_geo.categories.fold_text``, the
      unaccent fold the entity layer dedupes with — a superset of the
      đ/Đ-only ``searchstore.store.fold_d``) plus a province/ward hint:
      when both sides carry ``province`` (or ``area_old``) the folded
      values must agree; a missing hint on either side never blocks;
  (c) token-Jaccard over folded name tokens >= 0.80 (the store's
      ``entity_token_overlap`` metric) AND corroborated by address
      token-overlap >= 0.5 OR haversine(lat,lng) <= 200 m (geo applies
      only when both records carry coordinates).

Scores: 1.0 for rules (a)/(b), the name-Jaccard rounded to 4 decimals for
rule (c). Method strings: ``tax_code``, ``name_exact``, ``fuzzy``.

Blocking (candidate generation — avoids the O(n²) all-pairs scan): an
entity joins one bucket per normalized tax_code and one bucket for the
first token of its folded name; only bucket-sharing pairs are evaluated.
Rules (a) and (b) can never be missed — equal keys land in the same
bucket — while rule (c) could in principle miss a >=0.8-similar pair
whose names differ in the very first token; that is the documented v1
recall/speed tradeoff.

``canonical`` inside a group = oldest ``fetched_at`` (ISO strings compare
chronologically for same-format stamps), ties -> lexicographic
``entity_id``. Idempotent: pairs already aliased in EITHER direction are
skipped (``aliases_existing``) so a re-run reports ``aliases_new == 0``.
``dry_run=True`` computes and counts everything but writes nothing.

CLI: ``python -m vn_geo.resolve run|report --db PATH [--dry-run] [--json]``
— exit 0 ok · 1 runtime error · 2 usage/IO.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import uuid

from searchstore import SearchStore, SearchStoreError
from searchstore import store as _store

from . import categories

ALIAS_EVENT_KIND = "entity_aliased"
JACCARD_THRESHOLD = 0.80
ADDRESS_OVERLAP_THRESHOLD = 0.5
GEO_THRESHOLD_M = 200.0

# Method strings in priority order (payload ``method`` values, §3 rules a-c).
METHODS = ("tax_code", "name_exact", "fuzzy")
_METHOD_RANK = {name: rank for rank, name in enumerate(METHODS)}

_NON_DIGIT_RE = re.compile(r"\D+")
_TOKEN_RE = re.compile(r"[0-9a-z]+")


# --------------------------------------------------------------------------- entity projection


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _entity(row) -> dict:
    """Match-ready projection of one ``entities`` view row."""
    name = str(row["name"] or "").strip()
    entity_id = str(row["entity_id"] or "").strip() or _store.entity_id_for(
        str(row["provider"] or ""),
        str(row["source_id"] or ""),
        name,
        str(row["address_text"] or ""),
    )
    tokens = _TOKEN_RE.findall(categories.fold_text(name))
    return {
        "doc_id": row["doc_id"],
        "entity_id": entity_id,
        "name": name,
        "address": str(row["address_text"] or "").strip(),
        "fetched_at": str(row["fetched_at"] or ""),
        "tax_key": _NON_DIGIT_RE.sub("", str(row["tax_code"] or "")),
        "folded_name": categories.fold_text(name),
        "first_token": tokens[0] if tokens else "",
        "province": categories.fold_text(row["province"] or ""),
        "ward": categories.fold_text(row["area_old"] or ""),
        "lat": _to_float(row["lat"]),
        "lng": _to_float(row["lng"]),
    }


def _load_entities(store: SearchStore) -> list[dict]:
    """Current entity docs as match-ready dicts (``entities`` view)."""
    rows = store.conn.execute(
        "SELECT doc_id, entity_id, name, tax_code, address_text, area_old, province,"
        " lat, lng, source_id, provider, fetched_at FROM entities ORDER BY doc_id"
    ).fetchall()
    return [_entity(r) for r in rows]


# --------------------------------------------------------------------------- matching


def _same_area_hint(a: dict, b: dict) -> bool:
    """Province/ward hint (rule b): a field present on BOTH sides must agree."""
    for key in ("province", "ward"):
        if a[key] and b[key] and a[key] != b[key]:
            return False
    return True


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters (mean earth radius, stdlib only)."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _geo_close(a: dict, b: dict) -> bool:
    """True when both records carry lat/lng and are <= 200 m apart."""
    if None in (a["lat"], a["lng"], b["lat"], b["lng"]):
        return False
    return _haversine_m(a["lat"], a["lng"], b["lat"], b["lng"]) <= GEO_THRESHOLD_M


def _match(a: dict, b: dict) -> tuple[str, float] | None:
    """Highest-priority rule that fires for the pair, else None (§3 a->c)."""
    if a["tax_key"] and a["tax_key"] == b["tax_key"]:
        return "tax_code", 1.0
    if a["folded_name"] and a["folded_name"] == b["folded_name"] and _same_area_hint(a, b):
        return "name_exact", 1.0
    score = _store.entity_token_overlap(a["name"], b["name"])
    if score >= JACCARD_THRESHOLD and (
        _store.entity_token_overlap(a["address"], b["address"]) >= ADDRESS_OVERLAP_THRESHOLD or _geo_close(a, b)
    ):
        return "fuzzy", round(score, 4)
    return None


# --------------------------------------------------------------------------- blocking + grouping


def _candidate_pairs(entities: list[dict]) -> list[tuple[int, int]]:
    """Candidate index pairs via the §3 blocking strategy: same tax_code
    bucket OR same first folded-name-token bucket. Sorted for determinism."""
    buckets: dict[tuple[str, str], list[int]] = {}
    for i, e in enumerate(entities):
        if e["tax_key"]:
            buckets.setdefault(("tax", e["tax_key"]), []).append(i)
        if e["first_token"]:
            buckets.setdefault(("tok", e["first_token"]), []).append(i)
    pairs: set[tuple[int, int]] = set()
    for idxs in buckets.values():
        for x in range(len(idxs)):
            for y in range(x + 1, len(idxs)):
                pairs.add((idxs[x], idxs[y]))
    return sorted(pairs)


def _matched_edges(entities: list[dict]) -> list[tuple[int, int, str, float]]:
    """(i, j, method, score) for every candidate pair that matches."""
    edges = []
    for i, j in _candidate_pairs(entities):
        hit = _match(entities[i], entities[j])
        if hit is not None:
            edges.append((i, j, hit[0], hit[1]))
    return edges


def _components(n: int, pairs: list[tuple[int, int]]) -> list[list[int]]:
    """Union-find connected components over matched pairs; multi-member only."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
    members: dict[int, list[int]] = {}
    for i in range(n):
        members.setdefault(find(i), []).append(i)
    return [sorted(m) for m in members.values() if len(m) > 1]


def _best_link(i: int, entities: list[dict], edges_of: dict) -> tuple[str, float]:
    """Strongest matched edge incident to *i*: lowest method rank, then
    highest score, then partner entity_id (a total deterministic order)."""

    def key(link) -> tuple:
        j, method, score = link
        return (_METHOD_RANK[method], -score, entities[j]["entity_id"])

    _j, method, score = min(edges_of[i], key=key)
    return method, score


def _link_key(member: dict) -> tuple:
    """Rank a member link: strongest method, then highest score, then id."""
    score = member.get("score")
    return (
        _METHOD_RANK.get(member.get("method"), len(METHODS)),
        -(score if isinstance(score, (int, float)) else 0.0),
        str(member.get("entity_id") or ""),
    )


def _groups(entities: list[dict], matched: list[tuple[int, int, str, float]]) -> list[dict]:
    """Alias groups: canonical = oldest fetched_at (tie -> lexicographic
    entity_id); each member carries the strongest edge that links it in."""
    edges_of: dict[int, list[tuple[int, str, float]]] = {}
    for a, b, method, score in matched:
        edges_of.setdefault(a, []).append((b, method, score))
        edges_of.setdefault(b, []).append((a, method, score))
    groups = []
    for comp in _components(len(entities), [(a, b) for a, b, _, _ in matched]):
        order = sorted(comp, key=lambda i: (entities[i]["fetched_at"], entities[i]["entity_id"]))
        members = []
        for i in order[1:]:
            method, score = _best_link(i, entities, edges_of)
            members.append({"entity_id": entities[i]["entity_id"], "method": method, "score": score})
        best = min(members, key=_link_key)
        groups.append(
            {
                "canonical_entity_id": entities[order[0]]["entity_id"],
                "members": members,
                "method": best["method"],
                "score": best["score"],
            }
        )
    groups.sort(key=lambda g: g["canonical_entity_id"])
    return groups


def _existing_alias_pairs(store: SearchStore) -> set[frozenset]:
    """Unordered {canonical, alias} pairs already recorded — any direction."""
    pairs: set[frozenset] = set()
    rows = store.conn.execute("SELECT payload FROM events WHERE kind = ?", (ALIAS_EVENT_KIND,))
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            continue
        canonical = str(payload.get("canonical_entity_id") or "")
        alias = str(payload.get("alias_entity_id") or "")
        if canonical and alias:
            pairs.add(frozenset((canonical, alias)))
    return pairs


# --------------------------------------------------------------------------- public api (contract §3)


def run(db_path, dry_run: bool = False) -> dict:
    """Resolve entities in *db_path*; emit ``entity_aliased`` events.

    Returns ``{"checked","groups","aliases_new","aliases_existing",
    "method_counts"}`` — ``groups`` is a list of
    ``{"canonical_entity_id","members","method","score"}`` dicts in the same
    shape :func:`report` emits, ``members`` a list of
    ``{"entity_id","method","score"}``. ``dry_run=True`` computes and
    counts everything but writes nothing.
    """
    with SearchStore(db_path, create=False) as store:
        entities = _load_entities(store)
        aliased = _existing_alias_pairs(store)
        groups = _groups(entities, _matched_edges(entities))
        run_id = f"resolve-{uuid.uuid4().hex[:12]}"
        aliases_new = 0
        aliases_existing = 0
        method_counts: dict[str, int] = {}
        for group in groups:
            canonical = group["canonical_entity_id"]
            for member in group["members"]:
                method_counts[member["method"]] = method_counts.get(member["method"], 0) + 1
                if canonical == member["entity_id"]:
                    continue
                if frozenset((canonical, member["entity_id"])) in aliased:
                    aliases_existing += 1
                    continue
                aliases_new += 1
                if not dry_run:
                    store.record_event(
                        ALIAS_EVENT_KIND,
                        {
                            "canonical_entity_id": canonical,
                            "alias_entity_id": member["entity_id"],
                            "method": member["method"],
                            "score": member["score"],
                            "run_id": run_id,
                        },
                    )
        return {
            "checked": len(entities),
            "groups": groups,
            "aliases_new": aliases_new,
            "aliases_existing": aliases_existing,
            "method_counts": method_counts,
        }


def report(db_path) -> list[dict]:
    """Alias groups reconstructed from persisted ``entity_aliased`` events.

    Each group: ``{"canonical_entity_id","members","method","score"}`` where
    ``members`` holds ``{"entity_id","method","score"}`` dicts (sorted by
    entity_id; the earliest event wins on duplicates) and the group
    method/score is the strongest member link.
    """
    groups: dict[str, dict[str, dict]] = {}
    with SearchStore(db_path, create=False) as store:
        rows = store.conn.execute(
            "SELECT payload FROM events WHERE kind = ? ORDER BY id", (ALIAS_EVENT_KIND,)
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            continue
        canonical = str(payload.get("canonical_entity_id") or "")
        alias = str(payload.get("alias_entity_id") or "")
        if not canonical or not alias or canonical == alias:
            continue
        members = groups.setdefault(canonical, {})
        if alias not in members:
            members[alias] = {
                "entity_id": alias,
                "method": str(payload.get("method") or ""),
                "score": payload.get("score"),
            }
    out = []
    for canonical in sorted(groups):
        members = sorted(groups[canonical].values(), key=lambda m: m["entity_id"])
        best = min(members, key=_link_key)
        out.append(
            {
                "canonical_entity_id": canonical,
                "members": members,
                "method": best["method"],
                "score": best["score"],
            }
        )
    return out


# --------------------------------------------------------------------------- cli


def _emit_error(exc: Exception, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"error: {exc}", file=sys.stderr)


def _cmd_run(args: argparse.Namespace) -> tuple[dict, list[str]]:
    result = run(args.db, dry_run=args.dry_run)
    payload = {"ok": True, "db": str(args.db), "dry_run": bool(args.dry_run), **result}
    human = [
        f"checked {result['checked']} entities: {len(result['groups'])} groups, "
        f"{result['aliases_new']} new aliases ({result['aliases_existing']} already aliased)"
        + (" [dry-run]" if args.dry_run else "")
    ]
    for g in result["groups"]:
        human.append(f"  {g['canonical_entity_id']} <- {len(g['members'])} member(s) [{g['method']}]")
    return payload, human


def _cmd_report(args: argparse.Namespace) -> tuple[dict, list[str]]:
    groups = report(args.db)
    payload = {"ok": True, "count": len(groups), "groups": groups}
    human = [f"{len(groups)} alias groups"]
    for g in groups:
        human.append(f"  {g['canonical_entity_id']} [{g['method']} {g['score']}] <- {len(g['members'])} member(s)")
    return payload, human


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vn_geo.resolve",
        description="Non-destructive entity resolution: emit entity_aliased events (contract §3)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="match entities and record entity_aliased events")
    p.add_argument("--db", required=True, metavar="PATH")
    p.add_argument("--dry-run", action="store_true", help="compute matches but write nothing")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("report", help="list alias groups from entity_aliased events")
    p.add_argument("--db", required=True, metavar="PATH")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return 0 ok · 1 runtime error · 2 usage/IO."""
    args = _build_parser().parse_args(argv)
    json_mode = bool(getattr(args, "json", False))
    try:
        payload, human = args.func(args)
    except SearchStoreError as exc:
        _emit_error(exc, json_mode)
        return 1
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError, sqlite3.Error) as exc:
        _emit_error(exc, json_mode)
        return 2
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for line in human:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
