# R15 wave-3 verification ledger — B2 (ultra + backend worker pool + separability)

- Task: R15-B2 (Devin), base `6abfc95`, branch `r15-b2` → merge `1a0a918`.
- Frozen contract: `analysis/r15-interfaces.md` §7 (+ §7.1a slot-cache amendment `45564e5`).
- Deliverables: `gateway/core/pool.py` (NEW), `gateway/core/ultra.py` (NEW),
  `gateway/core/engine.py` (deep-path ultra wiring + additive `close()`), `gateway/config.py`
  (3 additive knobs), 3 new test files.
- Gates (worktree): pytest exit 0 (full suite), ruff check + format clean.
- Orchestrator acceptance `_accept_r15b2.py`: **7/7 PASS** — barrier-proven parallel searches
  (max in-flight 2), parallel extract batches (3 in flight), ultra-off legacy (pool untouched,
  B1 call order), failure isolation + serial retry warning, pool-dead serial fallback,
  engine.close() releases pool, plan_ultra partition math.
- Orchestrator fix (during review): pool rebuilt worker backends per map call → bridge
  subprocess churn per query; fixed to per-slot caching (§7.1a, `45564e5`) + reuse test.
- Merge `1a0a918`; CI + Security green (verified post-push).
