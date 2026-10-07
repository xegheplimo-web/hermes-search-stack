# R9-C / R15-A — Vietnamese Evaluation Corpus (v0 + v1)

Durable benchmark of realistic Vietnamese user queries for continuous
quality work on hermes-search-stack (law/decrees, admin procedures, local
places, news, gold/fuel prices, weather).

- Data: `corpus_v0.jsonl` — exactly 50 lines (frozen, schema v0).
- Data: `corpus_v1.jsonl` — 68 lines (schema v2): all 50 v0 cases preserved
  (ids/queries/`must_include` unchanged) **plus 18 NEW harder cases**
  (vn-051…vn-068: multi-part VN, freshness-sensitive, all with `variants`).
  Each case carries `severity` (S0–S3) and `ground_truth_source`.
- These files are the **schema reference + verification guide**. Wave 2 assigns
  live ground truth; this wave only defines the questions, expectations, and
  where to verify them.

## 1. Schema

Every line has ALL keys:

| Key | Type | Meaning |
|---|---|---|
| `id` | string `vn-001`..`vn-050` | stable case id |
| `domain` | enum | `law` (luật/nghị định) · `tax` (thuế) · `insurance` (BHXH/BHYT) · `residence` (cư trú/CCCD) · `boundary` (đơn vị hành chính sau sáp nhập 2025) · `transport` (giao thông/bằng lái/phạt nguội/metro) · `places` (bệnh viện/trường/chợ) · `gold` (giá vàng) · `fuel` (giá xăng dầu) · `weather` (thời tiết/bão) · `stores` (cửa hàng/dịch vụ) · `news` (tin tức/lịch nghỉ lễ) |
| `difficulty` | enum | `easy` direct factual/FAQ/strong local data · `medium` government info, well-defined local search, ordinary comparison · `hard` multi-doc reasoning, old-vs-new law, conflicting sources, ambiguous jurisdiction/entity, boundary changes, retrieval+calculation, or correct-behavior-is-to-refuse-unsupported-precision |
| `query` | string | main query: realistic Vietnamese user phrasing (with diacritics) |
| `variants` | string[] | same-intent rephrasings for language stress; `[]` if none |
| `intent` | string | one-sentence intent (what a correct answer must satisfy) |
| `expected.must_include` | string[] | facts/behaviors a passing answer must contain (conservative where uncertain) |
| `expected.must_not_include` | string[] | traps: superseded rules stated as current, wrong jurisdiction, fabricated addresses/hours, invented penalties — a hit here fails the case |
| `expected.required_fields` | string[] | fields the answer object must expose (e.g. `answer`, `citations`, `as_of`, `price`, `legal_basis`, `address`) |
| `ground_truth.status` | enum | `stable` timeless facts · `dynamic` prices/weather/news (answer only valid at a point in time) · `verify-later` cannot be asserted confidently now |
| `ground_truth.source` | string | authoritative source to verify against in wave 2; `TBD` for `verify-later` where the exact article/lookup is not yet pinned |
| `ground_truth.checked_at` | null | always `null` in v0; wave 2 fills in verification timestamp |
| `notes` | string | why the case exists / what makes it hard / what wave 2 must do |

### Schema v2 (corpus_v1.jsonl)

`corpus_v1.jsonl` adds two keys to every case (v0 cases preserved with
ids/queries/`must_include` unchanged):

| Key | Type | Meaning |
|---|---|---|
| `severity` | enum | `S0` wrong answer → direct financial/legal/safety harm (penalties, prices, taxes, insurance, weather safety, misinformation) · `S1` significant inconvenience (procedures, addresses, hours, boundaries) · `S2` moderate (comparisons, general local info) · `S3` trivial (sports results) |
| `ground_truth_source` | enum | `official` ground truth from an official/government/operator source · `human-reviewed` a human reviewed/annotated the expectation · `key` keyed to a specific verifiable record (decree/article/procedure) that still needs live verification · `derived` computed/calculated from other ground truth. **Never a model.** |

Severity weights (runner v2): `S0×4, S1×2, S2×1, S3×0.5`.

Rules applied: **never invent precise numbers/dates** — uncertain cases use
`verify-later` and conservative `must_include` (e.g. "nêu mức phạt theo khung
hiện hành kèm căn cứ pháp lý" instead of a đồng figure).

Anchor cases: `vn-001` = Nghị định 168/2024 traffic-penalty question modeled on
"nghi dinh 168 phat bao nhieu"; `vn-036` = old-vs-new comparison
NĐ 100/2019 vs 168/2024.

## 2. Distributions

### Difficulty × domain (counts)

| domain \ difficulty | easy | medium | hard | total |
|---|---|---|---|---|
| law | 1 (vn-001) | 3 (vn-016,017,029) | 3 (vn-036,037,047) | 7 |
| tax | 2 (vn-008,015) | 2 (vn-018,031) | 1 (vn-041) | 5 |
| insurance | 1 (vn-009) | 2 (vn-019,032) | 1 (vn-040) | 4 |
| residence | 1 (vn-007) | 2 (vn-020,033) | 1 (vn-048) | 4 |
| boundary | 0 | 1 (vn-021) | 2 (vn-038,039) | 3 |
| transport | 2 (vn-006,012) | 2 (vn-022,034) | 1 (vn-050) | 5 |
| places | 3 (vn-002,011,014) | 2 (vn-023,030) | 1 (vn-045) | 6 |
| gold | 1 (vn-004) | 1 (vn-024) | 1 (vn-042) | 3 |
| fuel | 1 (vn-005) | 1 (vn-025) | 1 (vn-043) | 3 |
| weather | 2 (vn-003,013) | 1 (vn-026) | 1 (vn-044) | 4 |
| stores | 1 (vn-010) | 1 (vn-027) | 1 (vn-049) | 3 |
| news | 0 | 2 (vn-028,035) | 1 (vn-046) | 3 |
| **total** | **15** | **20** | **15** | **50** |

Difficulty totals: easy 15 · medium 20 · hard 15 (exact).

Ground-truth status: `stable` 13 · `dynamic` 15 · `verify-later` 22
(count with `python -c`, see §5).

### Variants

15 cases carry ≥3 variants (requirement: ≥12):
`vn-001, vn-002, vn-003, vn-004, vn-005, vn-007, vn-009, vn-011, vn-016,
vn-020, vn-023, vn-034, vn-036, vn-040, vn-050`.
Each has 5 entries; the remaining 35 cases use `"variants": []`.

## 3. Variants → language-stress dimensions

Each variant set covers, in order, the same 5 dimensions (all same intent as
the main query):

1. **no-diacritics** — `nghi dinh 168 phat bao nhieu`, `benh vien ... o dau`
2. **abbreviation** — `ND168`, `ntn`, `bn`, `đc`, `BV`, `lcb`, `BT–ST`…
3. **common misspelling** — `đèn đõ`, `đia chỉ`, `hôm nai`, `dòn 95`, `chíp/vnied`, `dịch dụ`, `triêu`, `nhiu`…
4. **teencode/spoken** — `cho e hỏi … z ạ`, `ê … hong`, `mấy bà`, `mí giờ`, `8 củ`…
5. **EN-mixed** — `fine for running red light …?`, `hospital address …`,
   `gold price …`, `process …`, `operating hours …`

Wave-2 harness should run each variant as an independent probe and require the
same `must_include` / `must_not_include` verdicts as the main query.

## 4. Ground-truth verification (wave 2, live)

- `stable`: open `ground_truth.source`, confirm the fact + capture URL/doc
  section + set `checked_at`.
- `dynamic` (prices/weather/news/schedules): query at run time, record
  `as_of`, keep the cited figure + source snapshot; re-pin every run
  (gold: sjc.com.vn/pnj.vn; fuel: petrolimex/moi.gov.vn; weather: nchmf.gov.vn;
  news/sports: vff.org.vn/chinhphu.vn + named press).
- `verify-later` / `source: TBD`: resolve the exact article/resolution first
  (NĐ 168/2024 full text; NĐ 100/2019 for old-vs-new; sáp nhập resolutions;
  Luật BHXH 2024; current TNCN guidance; hospital/operator pages), then write
  the pinned numbers/dates into wave-2 ground truth — never back-fill from
  memory.
- Hard-case policy: when sources conflict or data is missing, the passing
  behavior is to **hedge, cite, or refuse the unsupported precision**
  (vn-040/041 calculation, vn-042 conflicting gold quotes, vn-044 7-day sea
  forecast, vn-045/049 fabricated POI/hours, vn-046 rumor).

## 4A. Reference-trace schema (R15-A gap scaffold)

The scoreboard gap scaffold compares the newest `results/r15_corpus_*.json`
run against reference traces under `results/reference/*.json`. A reference
trace is a battery-compatible JSON file (same shape the runner emits), e.g.:

```json
{
  "generated": "2026-10-07T00:00:00+00:00",
  "kind": "reference-trace",
  "model": "gpt-5.6-sol",
  "reasoning_effort": "xhigh",
  "cases": [
    {"id": "vn-001", "pass": true, "latency_s": 12.3},
    {"id": "vn-002", "pass": false, "latency_s": 9.1}
  ],
  "totals": {"pass": 1, "fail": 1, "total": 2, "elapsed_s": 21.4}
}
```

The scaffold computes `reference_pass_rate` (pass/total over the reference
cases) and `gap_pp` = `(reference_pass_rate − current_pass_rate) × 100`
(percentage points; positive = behind reference). When `results/reference/`
is missing or empty the scaffold warns on stderr and the scoreboard still
completes with exit 0 (no `gap` key is emitted). Reference traces are
produced offline (no network, no spending) — see `analysis/r15-plan.md` §5.

## 5. Validation commands (expected outputs)

```bash
python -c "import json;[json.loads(l) for l in open('evals/r9/corpus_v0.jsonl',encoding='utf-8')]"
# expected: no error (all 50 lines valid JSON)

python -c "import json,collections;rows=[json.loads(l) for l in open('evals/r9/corpus_v0.jsonl',encoding='utf-8')];print(len(rows));print(collections.Counter(r['difficulty'] for r in rows))"
# expected: 50
# expected: Counter({'medium': 20, 'easy': 15, 'hard': 15})

python -c "import json;rows=[json.loads(l) for l in open('evals/r9/corpus_v0.jsonl',encoding='utf-8')];n=sum(1 for r in rows if len(r.get('variants',[]))>=3);print('cases-with-3plus-variants:',n);assert n>=12"
# expected: cases-with-3plus-variants: 15

git status --short
# expected: only evals/r9/ additions, i.e.
#  ?? evals/r9/

## 6. Runner (`run_corpus.py`, R10-C/R15-A)

Runs the corpus against the read-only gateway and emits battery-compatible
artifacts under `results/` so `scripts/scoreboard.py` math applies.

```bash
# smoke (3 probes, no holdout)
python evals/r9/run_corpus.py --limit 3 --sleep 2

# wave baseline: default splits (regression + challenge, holdout excluded)
python evals/r9/run_corpus.py --sleep 2

# variants / holdout / filtering
python evals/r9/run_corpus.py --variants --sleep 2
python evals/r9/run_corpus.py --include-holdout --sleep 2
python evals/r9/run_corpus.py --ids vn-001,vn-003 --sleep 1

# offline check-logic validation (no HTTP)
python evals/r9/run_corpus.py --dry-run --limit 3 --json

# schema-v2 corpus (severity + variants)
python evals/r9/run_corpus.py --dry-run --corpus evals/r9/corpus_v1.jsonl --limit 3
python evals/r9/run_corpus.py --dry-run --corpus evals/r9/corpus_v1.jsonl --variants --limit 6
```

Flags: `--split` (default `regression,challenge`), `--corpus` (default
`evals/r9/corpus_v0.jsonl`; use `corpus_v1.jsonl` for schema v2),
`--include-holdout`, `--ids`, `--limit`, `--variants`, `--sleep` (default 2.0,
minimum 1.0), `--gateway` (default `http://127.0.0.1:8787`), `--out-dir` (default
`results/`), `--dry-run`, `--json` (prints the output JSON path).

Splits (frozen, see `splits.json`): holdout = ids where `int(id[3:]) % 10 == 5`
(`vn-005, vn-015, vn-025, vn-035, vn-045`), excluded unless
`--include-holdout`; challenge = `difficulty == "hard"` minus holdout;
regression = the rest.

Outputs: `results/r10_corpus_<ts>.json` (battery-compatible:
`{generated, run_command_*, live_calls, cases[{id,kind,input,pass,latency_s,
notes,error}], totals{pass,fail,total,elapsed_s}}`; corpus cases use
`kind: "corpus"` plus `signals` + `judge_pending`) and
`results/r10_corpus_<ts>.md` (per-difficulty / per-domain tables, latency
p50/p90 via `nearest_rank_percentile`, failure / judge-pending / stale
lists). R15-A also emits an identical `results/r15_corpus_<ts>.json/.md`
alias (read by the scoreboard gap scaffold) with v2 `aggregates`:
`{severity_weighted_pass_rate, p50_latency_s, p95_latency_s,
variant_consistency_avg}` plus per-case `severity` + `variant_consistency`.

Checks are signals, not scores: `citations_present`, `sources_section_present`,
`source_domains`, `required_fields` heuristics (`answer`, `citations`,
`as_of`, `price`, `unit`, `address`, `opening_hours`, `legal_basis` — anything
else is `judge-pending`), `must_include`/`must_not_include` normalized
substring match (casefold + whitespace collapse, diacritics preserved;
unmatched semantics → `judge-pending`, never a fabricated verdict), and a
freshness signal for `dynamic` ground truth. Stale dynamic cases are reported
separately and never counted as fail. `scoreboard.py` discovers
`r10_corpus_*.json` with the same math.
```
