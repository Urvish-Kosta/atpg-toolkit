"""The complete test generation flow.

Random patterns first, then deterministic ATPG on the remainder. This ordering
is not an optimisation detail, it is the whole economics of the problem: random
patterns cost almost nothing and typically detect 60-90% of faults, while PODEM
costs a search per fault. Running ATPG over the full fault list wastes most of
its effort on faults a coin flip would have caught.

Two forms of fault dropping matter, and the second is easy to omit:

  * after random presimulation, drop everything already detected;
  * after *each* deterministically generated pattern, fault-simulate it against
    the remaining list and drop everything it incidentally detects.

The second is where most of the saving is. A pattern generated for one fault
usually detects many others, so without this step the flow generates a pattern
per fault and produces test sets several times larger than necessary.

Reported metrics
----------------
Two numbers, and conflating them overstates results:

    fault coverage   = detected / total faults
    fault efficiency = (detected + proven redundant) / total faults

Efficiency is the fair measure of the *generator*, since no test exists for a
redundant fault. But it is only meaningful if "redundant" means proven, so
aborted faults are excluded from both numerators and reported separately.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .circuit import Circuit
from .faults import collapse
from .fsim import ParallelFaultSimulator, random_patterns
from .podem import Podem, Status
from .sim import Fault


@dataclass
class FlowResult:
    circuit_name: str
    n_faults: int
    detected: set[tuple[int, int]] = field(default_factory=set)
    redundant: list[Fault] = field(default_factory=list)
    aborted: list[Fault] = field(default_factory=list)
    patterns: list[list[int]] = field(default_factory=list)
    n_random: int = 0
    n_random_detected: int = 0
    n_atpg_patterns: int = 0
    n_sat_patterns: int = 0
    unknown: list[Fault] = field(default_factory=list)
    compacted_from: int = 0
    seconds: float = 0.0

    @property
    def coverage(self) -> float:
        return len(self.detected) / self.n_faults if self.n_faults else 0.0

    @property
    def efficiency(self) -> float:
        if not self.n_faults:
            return 0.0
        return (len(self.detected) + len(self.redundant)) / self.n_faults

    def summary(self) -> str:
        return (
            f"{self.circuit_name}: {len(self.detected)}/{self.n_faults} detected "
            f"({self.coverage:.2%} coverage, {self.efficiency:.2%} efficiency)\n"
            f"  patterns        : {len(self.patterns)} "
            f"({self.n_random} random + {self.n_atpg_patterns} PODEM"
            + (f" + {self.n_sat_patterns} SAT" if self.n_sat_patterns else "")
            + (f", compacted from {self.compacted_from}"
               if self.compacted_from else "") + ")\n"
            f"  random detected : {self.n_random_detected}\n"
            f"  redundant       : {len(self.redundant)} (proven untestable)\n"
            f"  aborted         : {len(self.aborted)} (PODEM gave up)\n"
            f"  unknown         : {len(self.unknown)} (SAT timed out; NOT counted "
            f"as redundant)\n"
            f"  time            : {self.seconds:.1f} s"
        )


def compact(circuit: Circuit, patterns: list[list[int]],
            faults: list[Fault]) -> list[list[int]]:
    """Reverse-order restoration compaction.

    Walk the pattern set from the end, keeping a pattern only if it detects some
    fault not already covered by the patterns kept so far. Later patterns come
    from harder faults and tend to be more specific, so processing in reverse
    keeps the high-value ones and discards early general patterns whose work has
    been subsumed.

    This is static compaction: it never invents a pattern, only drops redundant
    ones, so the resulting set provably detects exactly the same fault set. That
    property is asserted in the test suite -- a compactor that loses coverage is
    worse than no compactor.
    """
    fsim = ParallelFaultSimulator(circuit)
    kept: list[list[int]] = []
    covered: set[tuple[int, int]] = set()
    remaining = list(faults)

    for pattern in reversed(patterns):
        hit = fsim.run([pattern], remaining, drop=False).detected
        new = hit - covered
        if new:
            kept.append(pattern)
            covered |= new
            remaining = [f for f in remaining if (f.line, f.value) not in covered]
    kept.reverse()
    return kept


def run_flow(
    circuit: Circuit,
    *,
    n_random: int = 1024,
    backtrack_limit: int = 100,
    seed: int = 1,
    do_compact: bool = True,
    dominance: bool = False,
    max_faults: int | None = None,
    use_sat: bool = True,
    sat_budget: int = 200_000,
) -> FlowResult:
    """Random presimulation, then PODEM, then SAT on whatever is left.

    The three stages are ordered by cost per fault and each cleans up after the
    one before it. Random patterns are nearly free and remove the easy majority.
    PODEM is cheap per fault when a test exists but cannot terminate on
    redundant ones. SAT is the most expensive per call and is the only stage
    that can *prove* untestability, so it runs last and only on the residue --
    which is exactly the set of faults that are either redundant or hard.

    Without the SAT stage the ABORTED bucket stays populated and fault
    efficiency is unreportable, because an aborted fault is neither detected nor
    proven redundant.
    """
    started = time.time()
    faults = collapse(circuit, dominance=dominance).collapsed
    if max_faults is not None:
        faults = faults[:max_faults]

    result = FlowResult(circuit.name, len(faults), n_random=n_random)
    fsim = ParallelFaultSimulator(circuit)

    # -- stage 1: random patterns -----------------------------------------
    rand = random_patterns(circuit, n_random, seed=seed)
    rand_result = fsim.run(rand, faults, drop=True)
    result.detected |= rand_result.detected
    result.n_random_detected = len(rand_result.detected)
    used_random = sorted({p for p in rand_result.first_detection.values()})
    patterns = [rand[i] for i in used_random]
    result.n_random = len(patterns)

    # -- stage 2: deterministic ATPG on what is left ----------------------
    podem = Podem(circuit, backtrack_limit=backtrack_limit)
    pending = [f for f in faults if (f.line, f.value) not in result.detected]

    while pending:
        target = pending.pop(0)
        if (target.line, target.value) in result.detected:
            continue
        outcome = podem.generate(target)

        if outcome.status is Status.DETECTED:
            patterns.append(outcome.pattern)
            result.n_atpg_patterns += 1
            # Drop every fault this pattern happens to catch, not just the
            # target. This is where the pattern count collapses.
            #
            # Faults already parked as ABORTED must be included here. Excluding
            # them means a later pattern that happens to detect one never gets
            # the credit, so the flow reports lower coverage than the test set
            # it actually delivers -- an undercount, but still a number that
            # does not describe the artifact being shipped.
            candidates = pending + result.aborted + [target]
            hit = fsim.run([outcome.pattern], candidates, drop=False).detected
            result.detected |= hit
            result.detected.add((target.line, target.value))
            pending = [f for f in pending if (f.line, f.value) not in result.detected]
            result.aborted = [f for f in result.aborted
                              if (f.line, f.value) not in result.detected]
        elif outcome.status is Status.REDUNDANT and not use_sat:
            # Only trusted when there is no SAT stage to confirm it. PODEM
            # reports REDUNDANT whenever its search fails without exhausting the
            # backtrack budget, and that is not the same as proving no test
            # exists: three separate bugs in this project produced exactly this
            # verdict for perfectly testable faults. When SAT is available every
            # undetected fault goes to it for adjudication -- PODEM proposes,
            # SAT proves.
            result.redundant.append(target)
        else:
            result.aborted.append(target)

    # -- stage 3: SAT on the residue --------------------------------------
    if use_sat and result.aborted:
        from .satatpg import SatAtpg

        sat = SatAtpg(circuit)
        leftover = result.aborted
        result.aborted = []
        for target in leftover:
            if (target.line, target.value) in result.detected:
                continue
            outcome = sat.solve(target, conflict_budget=sat_budget)
            if outcome.testable:
                patterns.append(outcome.pattern)
                result.n_sat_patterns += 1
                hit = fsim.run([outcome.pattern], leftover, drop=False).detected
                result.detected |= hit
                result.detected.add((target.line, target.value))
            elif outcome.redundant:
                result.redundant.append(target)
            else:
                result.unknown.append(target)

    # -- stage 4: compaction ----------------------------------------------
    if do_compact and patterns:
        before = len(patterns)
        patterns = compact(circuit, patterns, faults)
        result.compacted_from = before

    result.patterns = patterns

    # Final reconciliation against the delivered pattern set. Incremental
    # bookkeeping is easy to get subtly wrong, so the reported coverage is
    # recomputed from the artifact rather than trusted from the accounting.
    final = fsim.run(patterns, faults, drop=True)
    overclaimed = result.detected - final.detected
    if overclaimed:
        raise AssertionError(
            f"{circuit.name}: {len(overclaimed)} faults claimed detected are not "
            "detected by the delivered pattern set"
        )
    result.detected = final.detected
    for bucket in ("aborted", "redundant", "unknown"):
        setattr(result, bucket, [f for f in getattr(result, bucket)
                                 if (f.line, f.value) not in result.detected])
    if result.redundant:
        # A fault proven untestable must never turn out to be detected by the
        # delivered pattern set. If it does, either the proof or the simulator
        # is wrong, and silently preferring one over the other would hide a
        # contradiction between two engines that are supposed to agree.
        proven = {(f.line, f.value) for f in result.redundant}
        assert not (proven & result.detected), (
            f"{circuit.name}: faults proven redundant were detected")

    result.seconds = time.time() - started
    return result


def verify_flow(circuit: Circuit, result: FlowResult,
                dominance: bool = False) -> tuple[bool, int]:
    """Independently re-simulate the final pattern set against the full fault
    list.

    The flow tracks detection incrementally as it goes; this recomputes it from
    scratch against the delivered patterns. If the two disagree, the reported
    coverage is a bookkeeping artifact rather than a property of the test set --
    which is precisely the kind of error that makes a tool's headline number
    worthless.
    """
    faults = collapse(circuit, dominance=dominance).collapsed
    check = ParallelFaultSimulator(circuit).run(result.patterns, faults, drop=True)
    return check.detected == result.detected, len(check.detected)
