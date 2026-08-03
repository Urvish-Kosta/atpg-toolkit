# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-03

Initial release.

### Added

- ISCAS `.bench` parser with Kahn levelisation, combinational-loop detection, and
  explicit fault-site enumeration over PI, stem, and fanout-branch lines.
- Bit-parallel logic simulator, 64 patterns per word, with stem and branch fault
  injection.
- Fault universe generation with structural equivalence collapsing (default) and
  optional dominance collapsing.
- Two fault simulators: a naive reference engine used as executable
  specification, and an event-driven, fault-dropping, bit-parallel engine.
- PODEM test generation over a (good, faulty) three-valued pair algebra, with
  X-path pruning and fanout-cone restriction.
- SAT-based test generation and redundancy proof via a Tseitin-encoded miter,
  with deterministic CDCL conflict budgets.
- Hybrid flow: random presimulation, PODEM, SAT adjudication of the residue, and
  static reverse-order-restoration compaction, with final re-simulation of the
  delivered pattern set.
- CLI (`info`, `fsim`, `atpg`, `redundancy`), benchmark fetch script, results
  regeneration script, 61-test suite, and CI across Python 3.10-3.12.

### Validated

- Equivalence-collapsed fault counts match published figures on all 11 ISCAS-85
  circuits; independently enumerated line counts reproduce the ISCAS naming
  convention.
- Redundancy counts match published figures on 10 of 11 circuits, each proven by
  UNSAT. c6288 reports 13 proven and 21 unknown against a published 34.
- c17 verified exhaustively: 22 collapsed faults, 22 distinct behaviours, zero
  undetectable.
- Every generated pattern confirmed by a fault simulator sharing no code with the
  generator.

### Fixed during initial development

Seven bugs, five of them false claims of proven untestability. See the README for
full detail: detection missed at the fault seeding site; five-valued X-path
criterion misapplied to the pair algebra; branch faults never registering as
activated; only the first D-frontier gate being tried; fanout-cone optimisation
breaking stem-fault injection; aborted faults excluded from fault dropping; and
PODEM's redundancy verdict being trusted as a proof.
