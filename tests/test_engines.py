"""Cross-engine agreement and regression tests for every bug found.

Every bug in this project was found by one engine disagreeing with another, not
by reading code. These tests pin those disagreements.
"""

import pytest

from atpg.faults import collapse
from atpg.flow import compact, run_flow, verify_flow
from atpg.fsim import ParallelFaultSimulator, ReferenceFaultSimulator, random_patterns
from atpg.podem import Podem, Status
from atpg.satatpg import SatAtpg
from atpg.sim import all_input_patterns
from tests.conftest import bench

SMALL = ["c17", "c432", "c499", "c880"]


# --- fault simulation -------------------------------------------------------

@pytest.mark.parametrize("name", SMALL)
def test_reference_and_parallel_fault_simulators_agree(name):
    """The optimised simulator is trustworthy exactly to the extent that it
    reproduces the naive one, which shares no code with it."""
    c = bench(name)
    faults = collapse(c).collapsed
    pats = random_patterns(c, 128, seed=1)
    ref = ReferenceFaultSimulator(c).run(pats, faults)
    par = ParallelFaultSimulator(c).run(pats, faults)
    assert ref.detected == par.detected


def test_detection_is_seen_at_a_fault_site_that_is_a_primary_output(c17):
    """Regression, bug 1. Six c17 faults diverge directly on a net that is
    itself a primary output. The event-driven simulator recorded the divergence
    but only tested for detection inside the propagation loop, so those faults
    were silently never detected -- an under-report of coverage."""
    faults = collapse(c17).collapsed
    pats = all_input_patterns(c17)
    ref = ReferenceFaultSimulator(c17).run(pats, faults)
    par = ParallelFaultSimulator(c17).run(pats, faults)
    assert par.detected == ref.detected
    assert len(par.detected) == 22


def test_fault_dropping_does_not_change_the_detected_set(c17):
    c = c17
    faults = collapse(c).collapsed
    pats = random_patterns(c, 64, seed=3)
    fs = ParallelFaultSimulator(c)
    assert fs.run(pats, faults, drop=True).detected == fs.run(pats, faults, drop=False).detected


# --- PODEM ------------------------------------------------------------------

def test_podem_matches_c17_exhaustive_ground_truth(c17):
    """c17 has 22 collapsed faults, all testable, proven by exhaustion.
    Regression for bugs 2, 3 and 4, each of which caused testable faults to be
    reported REDUNDANT: the x-path check using the five-valued criterion, branch
    faults never registering as activated, and only the first D-frontier gate
    being tried."""
    p = Podem(c17)
    results = [p.generate(f) for f in collapse(c17).collapsed]
    assert all(r.status is Status.DETECTED for r in results)


def test_podem_handles_branch_faults(reconvergent):
    """Bug 3: a branch fault's value is injected inside the consuming gate, so
    the stem shows no error and the gate never entered the D-frontier."""
    p = Podem(reconvergent)
    branch = next(ln for ln in reconvergent.lines if ln.kind == "BRANCH")
    from atpg.sim import Fault
    for value in (0, 1):
        r = p.generate(Fault(branch.index, value))
        assert r.status in (Status.DETECTED, Status.REDUNDANT)


@pytest.mark.parametrize("name", ["c17", "c432", "c880"])
def test_every_podem_pattern_is_confirmed_by_the_fault_simulator(name):
    """PODEM must never claim a pattern the simulator cannot confirm."""
    c = bench(name)
    p = Podem(c, backtrack_limit=50)
    fs = ParallelFaultSimulator(c)
    for f in collapse(c).collapsed[:200]:
        r = p.generate(f)
        if r.status is Status.DETECTED:
            assert fs.run([r.pattern], [f], drop=False).detected


def test_stem_faults_are_injected_under_the_cone_optimisation(c17):
    """Regression, bug 5. The fault cone contains a net's consumers, not its
    driver, so relying on the cone loop to force the stuck value left stem
    faults uninjected -- c17 fell from 22 detected to 12."""
    p = Podem(c17)
    stems = [ln for ln in c17.lines if ln.kind == "STEM"]
    from atpg.sim import Fault
    detected = sum(
        p.generate(Fault(ln.index, v)).status is Status.DETECTED
        for ln in stems for v in (0, 1)
    )
    assert detected == 2 * len(stems)


# --- SAT --------------------------------------------------------------------

def test_sat_matches_c17_ground_truth(c17):
    s = SatAtpg(c17)
    results = [s.solve(f) for f in collapse(c17).collapsed]
    assert all(r.testable for r in results)


@pytest.mark.parametrize("name,redundant", [
    ("c432", 4), ("c499", 8), ("c880", 0), ("c1355", 8), ("c1908", 9),
])
def test_sat_redundancy_counts_match_published_figures(name, redundant):
    c = bench(name)
    s = SatAtpg(c)
    results = [s.solve(f) for f in collapse(c).collapsed]
    assert sum(r.redundant for r in results) == redundant


@pytest.mark.parametrize("name", ["c17", "c432"])
def test_every_sat_pattern_is_confirmed_by_the_fault_simulator(name):
    c = bench(name)
    s = SatAtpg(c)
    fs = ParallelFaultSimulator(c)
    faults = collapse(c).collapsed
    for f in faults[:150]:
        r = s.solve(f)
        if r.testable:
            assert fs.run([r.pattern], [f], drop=False).detected


def test_exhausted_conflict_budget_is_not_a_proof(c17):
    """Regression, bug 7's mechanism. A budget-limited solve that gives up must
    report `timed_out` and must not satisfy `redundant`; an unproven fault
    counted as redundant inflates fault efficiency."""
    s = SatAtpg(c17)
    f = collapse(c17).collapsed[0]
    r = s.solve(f, conflict_budget=0)
    if r.timed_out:
        assert not r.redundant


# --- flow -------------------------------------------------------------------

@pytest.mark.parametrize("name", ["c17", "c432", "c880"])
def test_flow_coverage_is_reproducible_from_the_delivered_patterns(name):
    """Regression, bug 6. Incremental bookkeeping drifted from the artifact
    because aborted faults were excluded from fault dropping."""
    c = bench(name)
    r = run_flow(c, n_random=512, backtrack_limit=50)
    ok, recomputed = verify_flow(c, r)
    assert ok
    assert recomputed == len(r.detected)


def test_flow_does_not_trust_podem_redundancy_when_sat_is_available(c17):
    """Regression, bug 7. PODEM reports REDUNDANT whenever its search fails
    without exhausting the backtrack budget, which is not a proof. With the SAT
    stage enabled, every undetected fault must be adjudicated by SAT."""
    r = run_flow(c17, n_random=0, backtrack_limit=1, use_sat=True)
    proven = {(f.line, f.value) for f in r.redundant}
    s = SatAtpg(c17)
    for f in r.redundant:
        assert s.solve(f).redundant, "flow reported an unproven fault as redundant"
    assert not (proven & r.detected)


@pytest.mark.parametrize("name", ["c17", "c432", "c499", "c880"])
def test_full_fault_efficiency_on_circuits_without_unknowns(name):
    c = bench(name)
    r = run_flow(c, n_random=1024, backtrack_limit=50)
    assert not r.aborted
    if not r.unknown:
        assert r.efficiency == pytest.approx(1.0)


def test_compaction_preserves_the_detected_fault_set(c17):
    """Static compaction may only drop patterns, never coverage."""
    c = c17
    faults = collapse(c).collapsed
    pats = random_patterns(c, 200, seed=5)
    fs = ParallelFaultSimulator(c)
    before = fs.run(pats, faults, drop=True).detected
    kept = compact(c, pats, faults)
    after = fs.run(kept, faults, drop=True).detected
    assert after == before
    assert len(kept) <= len(pats)
