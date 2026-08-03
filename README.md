# atpg-toolkit

**Stuck-at fault simulation and automatic test pattern generation for combinational circuits.**

[![CI](https://github.com/Urvish-Kosta/atpg-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/Urvish-Kosta/atpg-toolkit/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Four engines — a reference fault simulator, a bit-parallel one, PODEM, and a
SAT-based prover — that check each other's work, validated against the ISCAS-85
benchmark suite.

```
$ atpg atpg c3540
c3540: 3291/3428 detected (96.00% coverage, 100.00% efficiency)
  patterns        : 177 (139 random + 38 PODEM, compacted from 331)
  redundant       : 137 (proven untestable)
  aborted         : 0 (PODEM gave up)
  unknown         : 0 (SAT timed out; NOT counted as redundant)
  time            : 10.6 s
  independent re-simulation of the delivered patterns: CONFIRMED (3291 detected)
```

That 137 is the published redundant-fault count for c3540, and every one of them
is proven by UNSAT rather than assumed.

---

## Table of contents

- [What this is](#what-this-is)
- [Results](#results)
- [Why the validation is the point](#why-the-validation-is-the-point)
- [Bugs found by running the code](#bugs-found-by-running-the-code)
- [Architecture](#architecture)
- [Installation](#installation)
- [Usage](#usage)
- [Design decisions](#design-decisions)
- [Limitations](#limitations)
- [Future work](#future-work)
- [References](#references)
- [License](#license)

## What this is

Given a gate-level combinational netlist, the toolkit answers three questions:

1. **Which faults does a given pattern set detect?** (fault simulation)
2. **What pattern set detects as many faults as possible?** (test generation)
3. **For the rest — is there no test, or did we just not find one?** (redundancy)

The third question is the one that separates a real tool from a demo. A fault
with no possible test is *redundant*, and no generator can be blamed for missing
it. A fault the generator merely failed on is *aborted*. Reporting the second as
the first inflates fault efficiency, and it is the easiest number in this field
to quietly get wrong. Here, redundancy is only ever claimed on the strength of
an UNSAT proof.

## Results

Full ISCAS-85 suite. Coverage is over the equivalence-collapsed fault list;
efficiency counts proven-redundant faults as legitimately untestable.

| circuit | gates | faults | coverage | efficiency | patterns | redundant | unknown | published | time |
|---|---|---|---|---|---|---|---|---|---|
| c17 | 6 | 22 | 100.00% | 100.00% | 6 | 0 | 0 | 0 ✓ | 0.0s |
| c432 | 160 | 524 | 99.24% | 100.00% | 62 | 4 | 0 | 4 ✓ | 0.2s |
| c499 | 202 | 758 | 98.94% | 100.00% | 55 | 8 | 0 | 8 ✓ | 0.3s |
| c880 | 383 | 942 | 100.00% | 100.00% | 66 | 0 | 0 | 0 ✓ | 0.2s |
| c1355 | 546 | 1574 | 99.49% | 100.00% | 88 | 8 | 0 | 8 ✓ | 0.8s |
| c1908 | 880 | 1879 | 99.52% | 100.00% | 126 | 9 | 0 | 9 ✓ | 1.4s |
| c2670 | 1193 | 2747 | 95.74% | 100.00% | 143 | 117 | 0 | 117 ✓ | 8.8s |
| c3540 | 1669 | 3428 | 96.00% | 100.00% | 177 | 137 | 0 | 137 ✓ | 10.6s |
| c5315 | 2307 | 5350 | 98.90% | 100.00% | 143 | 59 | 0 | 59 ✓ | 4.0s |
| c7552 | 3512 | 7550 | 98.26% | 100.00% | 265 | 131 | 0 | 131 ✓ | 41.0s |
| c6288 | 2416 | 7744 | 99.56% | **99.73%** | 34 | 13 | **21** | 34 (13+21) | 238.3s |

**Ten of eleven circuits reach 100% fault efficiency with every redundancy
SAT-proven and matching the published count.**

c6288 is reported honestly at 99.73%. It is a 16×16 multiplier of depth 124 and
the textbook-hard case for ATPG. Its testable count of 7710 matches the
literature exactly, and 13 proven + 21 unknown = 34, the published redundant
count — so the 21 are almost certainly redundant. They are still reported as
*unknown*, because "the arithmetic works out" is not a proof, and a per-fault
budget of ten million conflicts resolves only a handful more.

Equivalence-collapsed fault counts also match published figures on all eleven
circuits, and the parser reproduces the ISCAS naming convention: each circuit's
independently enumerated line count equals its name (c17 → 17 lines, c7552 →
7552). That is a structural check on the fault-site model that could not have
been arranged after the fact.

## Why the validation is the point

Writing a PODEM implementation is a known exercise. Knowing whether it is
*correct* is the hard part, because ATPG has a failure mode that looks like
success: a generator that gives up early reports faults as redundant, fault
efficiency goes **up**, and nothing crashes.

So the toolkit is built as four engines that check each other:

| check | what it catches |
|---|---|
| Reference vs bit-parallel fault simulator | optimisation bugs in the fast engine |
| Every generated pattern re-simulated by the fault simulator | false claims of detection |
| c17 exhaustive enumeration (32 patterns, all 34 faults) | false claims of redundancy |
| SAT UNSAT proof vs PODEM's verdict | PODEM giving up and calling it a proof |
| Published ISCAS-85 collapsed and redundant counts | systematic errors in the fault model |
| Final re-simulation of the delivered pattern set | bookkeeping drift in the flow |

Five of the seven bugs below were false claims of proven untestability. Not one
was found by reading code.

## Bugs found by running the code

1. **Fault simulator missed detections at the seeding site.** Six c17 faults
   diverge directly on a net that is itself a primary output; detection was only
   tested inside the propagation loop. Silent under-report of coverage.
   *Caught by the reference-vs-parallel differential.*

2. **X-path check used the five-valued criterion.** With the (good, faulty) pair
   algebra, a net with good = 1 and faulty = X can still diverge, so pruning on
   `good != X` blocked legitimate propagation paths. 11 testable c17 faults
   reported redundant. *Caught by exhaustive ground truth.*

3. **Branch faults never registered as activated.** A branch fault is injected
   inside the consuming gate, so the stem shows no error and the gate never
   entered the D-frontier — the frontier was empty at the exact moment the fault
   was excited. 5 more false redundants. *Caught by exhaustive ground truth.*

4. **Only the first D-frontier gate was tried.** Invisible on c17, catastrophic
   on c880: 165 testable faults declared redundant. This bug requires heavy
   reconvergent fanout to appear, so a test suite using only toy circuits would
   never have found it. *Caught by c880's known 100% testability.*

5. **Cone optimisation broke stem-fault injection.** The fanout cone contains a
   net's consumers, not its driver, so stem faults were never injected. c17 fell
   from 22 detected to 12 within seconds of the optimisation landing.
   *Caught by exhaustive ground truth.*

6. **Aborted faults excluded from fault dropping.** Once parked, a fault could
   never be credited to a later pattern, so the flow under-reported the coverage
   of the test set it actually shipped. *Caught by final re-simulation.*

7. **PODEM's redundancy verdict was trusted as fact.** The flow reported c6288
   at 100% efficiency with all 34 redundancies "proven". They were not: PODEM
   emits REDUNDANT whenever its search fails without exceeding the backtrack
   limit, which on c6288 takes milliseconds. Given that bugs 2, 3 and 4 were all
   false redundancy claims from that same verdict, wiring it into the output as
   fact was the worst mistake in the project. The design is now **PODEM
   proposes, SAT proves**. *Caught by refusing to accept a result that was too
   good: 6.5 s to prove what a standalone solve could not do in 400 s.*

Fixing 7 also replaced a wall-clock `threading.Timer` solver interrupt with a
deterministic CDCL conflict budget. `solve_limited` under a thread interrupt has
ambiguous return semantics, and a tool whose verdicts must be reproducible
cannot have run-to-run variance in them.

## Architecture

```
   .bench ──► circuit.py ─── parse, levelise, enumerate fault sites
                  │            (PI / STEM / BRANCH lines)
                  ▼
            faults.py ──── equivalence + dominance collapsing
                  │
      ┌───────────┼────────────────┬──────────────────┐
      ▼           ▼                ▼                  ▼
   sim.py     fsim.py          podem.py          satatpg.py
 bit-parallel  reference  │  PODEM search      Tseitin miter
   logic       + parallel │  (good,faulty)     + CDCL solver
              fault sim   │   pair algebra      UNSAT = proof
      │           │            │                     │
      └───────────┴────────────┴─────────────────────┘
                              │
                          flow.py ─── random ► PODEM ► SAT ► compaction
                              │        with fault dropping at each stage
                              ▼
                           cli.py
```

Stage ordering in `flow.py` is the economics of the problem: random patterns are
nearly free and remove the easy majority; PODEM is cheap per fault when a test
exists but cannot terminate on redundant ones; SAT is the most expensive per
call and the only stage that can prove untestability, so it runs last on the
residue — precisely the faults that are either redundant or hard.

## Installation

```bash
git clone https://github.com/Urvish-Kosta/atpg-toolkit.git
cd atpg-toolkit
python -m venv .venv && source .venv/bin/activate
pip install --pre -e ".[dev]"    # PySAT ships pre-release-tagged builds only

python scripts/fetch_benchmarks.py    # ISCAS-85, not vendored
pytest                                # 61 tests, ~34 s
```

The benchmarks are downloaded rather than committed: they are third-party
academic circuits and belong with their attribution, not as an unmarked copy.
The tests that need them skip cleanly if they are absent, so `pytest` is
meaningful on a fresh clone with no network.

## Usage

```bash
atpg info c3540                      # circuit and fault-list statistics
atpg fsim c880 -n 4096 --cross-check # fault simulation, both engines must agree
atpg atpg c2670 --write patterns.txt # full flow, writes the pattern set
atpg redundancy c1908                # per-fault UNSAT proof of untestability

atpg atpg c6288 --no-sat             # PODEM only; watch efficiency become unreportable
atpg atpg c432 --sat-budget 1000     # small budget; unknowns appear, honestly
```

As a library:

```python
from atpg.circuit import load_bench
from atpg.flow import run_flow, verify_flow

circuit = load_bench("benchmarks/c880.bench")
result = run_flow(circuit, n_random=2048)
print(result.summary())
assert verify_flow(circuit, result)[0]
```

## Design decisions

**Lines, not gates, as fault sites.** Where a net fans out, the stem and each
branch are separate faults: a fault on one branch is invisible on the others.
Collapsing them into one site under-counts the universe and inflates coverage.
A net driving exactly one gate gets no branch line, since stem and branch would
be identical.

**(good, faulty) pairs rather than five-valued logic.** D is (1, 0) and D' is
(0, 1); gate evaluation is plain three-valued logic applied twice. Easier to
read, harder to get wrong, and strictly more precise — five-valued logic
collapses (X, 0) to X and discards information PODEM can use. Bug 2 was the cost
of importing a five-valued heuristic into this algebra unchanged.

**PODEM rather than the D-algorithm.** PODEM branches only on primary inputs, so
every state is a legal input assignment by construction and implication is a
single forward simulation, with no justification step. On circuits with
reconvergent fanout this is far better behaved.

**Equivalence collapsing by default, dominance opt-in.** Equivalence is
symmetric and safe for coverage reporting. Dominance is asymmetric and only safe
if the dominated fault is actually targeted. Reporting a coverage figure that
quietly used dominance is not comparable with published equivalence-collapsed
numbers, so `--dominance` says so.

**Deterministic conflict budgets, not wall-clock timeouts.** Verdicts must be
reproducible run to run.

## Limitations

- **Combinational circuits only.** No DFFs, no scan chains, no sequential ATPG.
  `.bench` files containing DFF are rejected with an explicit message rather
  than silently mishandled.
- **Single stuck-at faults only.** No bridging, transition, delay, or
  open faults, and no multiple-fault models.
- **c6288 is not fully resolved.** 21 faults remain unknown; see Results.
- **Python throughout.** The bit-parallel simulator uses 64-wide word packing
  and is fast enough for ISCAS-85, but this is not a production-scale tool. c7552
  ATPG takes 41 s where a commercial tool takes well under a second.
- **No test compaction beyond static reverse-order restoration.** Dynamic
  compaction during generation would produce smaller sets.
- **Published-figure comparison is a check, not a proof.** Matching a literature
  count is strong evidence, not certainty; only the c17 exhaustive result and
  the UNSAT proofs are proofs.

## Future work

1. **Sequential ATPG** over ISCAS-89, with time-frame expansion. The largest
   functional gap and the one that matters most in industry, since real designs
   are scan-based.
2. **Transition and path-delay fault models.** At-speed test is where the volume
   of modern DFT work actually is.
3. **Resolve c6288** with incremental SAT — one solver instance across faults,
   reusing learned clauses instead of rebuilding the CNF 7744 times.
4. **Dynamic compaction**, merging compatible pattern requirements during
   generation rather than discarding patterns afterwards.
5. **Diagnostic fault dictionaries**, mapping observed failures back to
   candidate fault sites.
6. **X-tolerant compression** — MISR signatures and test cubes, connecting this
   to the on-chip compression hardware side of DFT.

## References

- F. Brglez and H. Fujiwara, "A Neutral Netlist of 10 Combinational Benchmark
  Circuits and a Target Translator in Fortran", *Proc. ISCAS*, 1985.
- P. Goel, "An Implicit Enumeration Algorithm to Generate Tests for
  Combinational Logic Circuits", *IEEE Trans. Computers*, C-30(3), 1981.
- J. P. Roth, "Diagnosis of Automata Failures: A Calculus and a Method",
  *IBM J. Research and Development*, 10(4), 1966.
- T. Larrabee, "Test Pattern Generation Using Boolean Satisfiability",
  *IEEE Trans. CAD*, 11(1), 1992.
- M. L. Bushnell and V. D. Agrawal, *Essentials of Electronic Testing for
  Digital, Memory and Mixed-Signal VLSI Circuits*, Springer, 2000.
- G. S. Tseitin, "On the Complexity of Derivation in Propositional Calculus",
  1968.

## License

MIT — see [LICENSE](LICENSE). The ISCAS-85 benchmark circuits are third-party
academic material and are downloaded, not redistributed here.

## Author

Urvish Kosta — embedded systems and digital design engineer.
[GitHub](https://github.com/Urvish-Kosta) · [LinkedIn](https://www.linkedin.com/in/urvish-kosta)
