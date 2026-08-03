import pytest

from atpg.circuit import BRANCH, PI, STEM, BenchParseError, parse_bench


def test_parses_inputs_outputs_and_gates(chain):
    assert chain.inputs == ["a", "b", "c"]
    assert chain.outputs == ["z"]
    assert chain.n_gates == 3


def test_levelisation_is_topological(chain):
    for gate in chain.gates:
        for net in gate.inputs:
            if net in chain.driver:
                assert chain.gates[chain.driver[net]].level < gate.level


def test_fanout_branches_only_where_fanout_exceeds_one(reconvergent):
    kinds = {}
    for line in reconvergent.lines:
        kinds.setdefault(line.kind, []).append(line)
    # n1 is the only net with fanout > 1 (it feeds n2 and n3).
    branch_nets = {ln.net for ln in kinds[BRANCH]}
    assert branch_nets == {"n1"}
    assert sum(1 for ln in kinds[BRANCH] if ln.net == "n1") == 2
    # a, b and c each feed exactly one gate, so they get no branch lines.
    for single in ("a", "b", "c"):
        assert single not in branch_nets


def test_line_count_is_pi_plus_stems_plus_branches(chain):
    pis = sum(1 for ln in chain.lines if ln.kind == PI)
    stems = sum(1 for ln in chain.lines if ln.kind == STEM)
    branches = sum(1 for ln in chain.lines if ln.kind == BRANCH)
    assert pis == len(chain.inputs)
    assert stems == chain.n_gates
    assert chain.n_lines == pis + stems + branches


@pytest.mark.parametrize("name,expected_lines", [
    ("c17", 17), ("c432", 432), ("c880", 880), ("c1355", 1355), ("c7552", 7552),
])
def test_iscas85_line_counts_match_their_names(name, expected_lines):
    """The ISCAS-85 circuits are named after their line counts. Reproducing the
    name from an independent enumeration of PIs, gate outputs and fanout
    branches is a strong external check on the fault-site model."""
    from tests.conftest import bench
    assert bench(name).n_lines == expected_lines


def test_rejects_undriven_net():
    with pytest.raises(BenchParseError, match="undriven"):
        parse_bench("INPUT(a)\nOUTPUT(z)\nz = AND(a, missing)")


def test_rejects_multiply_driven_net():
    with pytest.raises(BenchParseError, match="more than once"):
        parse_bench("INPUT(a)\nOUTPUT(z)\nz = NOT(a)\nz = BUFF(a)")


def test_rejects_combinational_loop():
    with pytest.raises(BenchParseError, match="loop"):
        parse_bench("INPUT(a)\nOUTPUT(z)\nn1 = AND(a, n2)\nn2 = BUFF(n1)\nz = BUFF(n2)")


def test_rejects_sequential_elements():
    with pytest.raises(BenchParseError, match="DFF"):
        parse_bench("INPUT(a)\nOUTPUT(z)\nz = DFF(a)")


def test_rejects_unknown_gate():
    with pytest.raises(BenchParseError, match="unsupported gate"):
        parse_bench("INPUT(a)\nOUTPUT(z)\nz = MUX(a, a)")
