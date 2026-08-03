# Contributing

```bash
python -m venv .venv && source .venv/bin/activate
pip install --pre -e ".[dev]"  # PySAT ships pre-release-tagged builds only
python scripts/fetch_benchmarks.py
pytest
```

## The rule that matters

**Never report a fault as redundant without a proof.**

ATPG has a failure mode that looks like success: a generator that gives up early
reports faults as redundant, fault efficiency goes *up*, and nothing crashes.
Five of the seven bugs documented in the README were exactly this. Therefore:

- A new search technique may report `ABORTED`. It may not report `REDUNDANT`
  unless the search provably ran to exhaustion.
- A budget-limited solve that gives up must set `timed_out`, and `timed_out`
  must never satisfy `redundant`.
- If you add an engine that classifies faults, add a test comparing it against
  c17's exhaustive ground truth (32 patterns settle every question) and against
  the published ISCAS-85 redundancy counts.

## Cross-checks are not optional

Every bug so far was found by one engine disagreeing with another. New code
should be checkable the same way:

- New fault-simulation optimisation → must agree with `ReferenceFaultSimulator`.
- New pattern generator → every pattern must be confirmed by a fault simulator
  that shares no code with it.
- New flow stage → `verify_flow` must still reproduce the reported coverage from
  the delivered patterns.

## Numbers in documentation

No result is typed in by hand. Generate it:

```bash
make results
```

## Style

- `ruff check src tests scripts` must pass.
- Comments explain *why*. The interesting content here is the reasoning about
  what a verdict does and does not license you to claim.
- Keep `circuit.py` free of timing and search concepts, and keep `analysis`-side
  code (`podem.py`, `satatpg.py`) free of assumptions about specific circuits.

## Commits

Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `perf:`,
`build:`, `ci:`, `chore:`. One logical change per commit.
