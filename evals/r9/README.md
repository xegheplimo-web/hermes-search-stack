# R9-C — Vietnamese Evaluation Corpus v0

Durable benchmark of **50 realistic Vietnamese user queries** for continuous
quality work on hermes-search-stack (law/decrees, admin procedures, local
places, news, gold/fuel prices, weather).

- Data: `corpus_v0.jsonl` — exactly 50 lines, one JSON object per line.
- This file is the **schema reference + verification guide**. Wave 2 assigns
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
```
