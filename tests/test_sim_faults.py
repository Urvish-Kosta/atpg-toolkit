import pytest

from atpg.faults import all_faults, collapse
from atpg.sim import Fault, Simulator, all_input_patterns, pack_patterns, unpack_outputs
from tests.conftest import bench


def evaluate(circuit, pattern, fault=None):
    vals = Simulator(circuit).simulate(pack_patterns(circuit, [pattern]), fault)
    return unpack_outputs(circuit, vals, 1)[0]


def test_chain_matches_hand_derived_logic(chain):
    for a in (0, 1):
        for b in (0, 1):
            for c in (0, 1):
                n1 = 1 - (a & b)
                n2 = 1 - (n1 & c)
                assert evaluate(chain, [a, b, c]) == [1 - n2]


def test_xor_chain(xor_circuit):
    for a in (0, 1):
        for b in (0, 1):
            for c in (0, 1):
                assert evaluate(xor_circuit, [a, b, c]) == [a ^ b ^ c]


def test_c17_exhaustive_against_independent_reference(c17):
    def ref(v):
        n1, n2, n3, n6, n7 = v
        nd = lambda x, y: 1 - (x & y)
        g10, g11 = nd(n1, n3), nd(n3, n6)
        g16, g19 = nd(n2, g11), nd(g11, n7)
        return [nd(g10, g16), nd(g16, g19)]

    pats = all_input_patterns(c17)
    got = unpack_outputs(c17, Simulator(c17).simulate(pack_patterns(c17, pats)), len(pats))
    assert all(g == ref(p) for p, g in zip(pats, got, strict=True))


def test_branch_fault_affects_one_consumer_only(reconvergent):
    """A stem fault forces the value everywhere it is used; a branch fault
    forces it at a single gate input, leaving siblings untouched.

    This is asserted on internal nets rather than primary outputs on purpose:
    in a small circuit the two can easily be output-equivalent, and testing the
    observable behaviour would then pass even if the injection logic conflated
    them. The structural property is what the fault model depends on.
    """
    branches = [ln for ln in reconvergent.lines
                if ln.kind == "BRANCH" and ln.net == "n1"]
    assert len(branches) == 2
    stem = reconvergent.line_of_net["n1"]
    sim = Simulator(reconvergent)
    to_n2 = next(b for b in branches
                 if reconvergent.gates[b.gate].output == "n2")

    # a=b=1 makes n1=1; c=0 stops c from masking the difference at n3.
    pattern = pack_patterns(reconvergent, [[1, 1, 0]])
    stem_vals = sim.simulate(pattern, Fault(stem, 0))
    branch_vals = sim.simulate(pattern, Fault(to_n2.index, 0))

    # Both perturb n2 identically...
    assert stem_vals["n2"] == branch_vals["n2"]
    # ...but only the stem fault reaches the sibling consumer n3.
    assert stem_vals["n3"] != branch_vals["n3"]


def test_fault_universe_is_two_per_line(chain):
    assert len(all_faults(chain)) == 2 * chain.n_lines


def test_c17_collapses_to_the_canonical_22(c17):
    assert collapse(c17).n_collapsed == 22


def test_c17_structural_collapse_equals_true_functional_collapse(c17):
    """Exhaustive proof that structural collapsing is optimal here: the number
    of distinct output behaviours over all 32 patterns equals the collapsed
    fault count, and no fault is undetectable."""
    sim = Simulator(c17)
    pats = all_input_patterns(c17)
    words = pack_patterns(c17, pats)
    good = tuple(map(tuple, unpack_outputs(c17, sim.simulate(words), 32)))
    behaviours = set()
    undetectable = 0
    for f in all_faults(c17):
        r = tuple(map(tuple, unpack_outputs(c17, sim.simulate(words, f), 32)))
        behaviours.add(r)
        undetectable += (r == good)
    assert undetectable == 0
    assert len(behaviours) == 22 == collapse(c17).n_collapsed


@pytest.mark.parametrize("name,expected", [
    ("c432", 524), ("c499", 758), ("c880", 942), ("c1355", 1574),
    ("c1908", 1879), ("c3540", 3428), ("c5315", 5350), ("c6288", 7744),
])
def test_collapsed_counts_match_published_figures(name, expected):
    assert collapse(bench(name)).n_collapsed == expected


def test_collapsing_never_grows_the_list(chain, reconvergent, xor_circuit):
    for c in (chain, reconvergent, xor_circuit):
        r = collapse(c)
        assert r.n_collapsed <= r.total
        assert all(rep in {(f.line, f.value) for f in r.collapsed}
                   for rep in r.representative.values())


def test_xor_gates_admit_no_structural_equivalence(xor_circuit):
    """XOR has no controlling value, so no input fault forces the output.
    Collapsing anything at an XOR would be unsound."""
    r = collapse(xor_circuit)
    assert r.n_collapsed == r.total
