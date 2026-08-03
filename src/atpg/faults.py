"""Stuck-at fault universe and structural fault collapsing.

Why collapse
------------
The uncollapsed universe is 2 x (number of lines). Many of those faults are
provably indistinguishable from one another by *any* test, so generating a
pattern for each is wasted work. Collapsing partitions the universe into
equivalence classes and keeps one representative per class, which typically
removes 35-45% of the faults at zero cost in coverage.

Two relations are implemented, and they are not interchangeable:

**Equivalence** is symmetric: two faults are equivalent when they produce
identical output responses under every input pattern. Structural equivalence at
a gate is the cheap, sound subset of this. For an AND gate, any input stuck at 0
and the output stuck at 0 force the same behaviour, so they form one class.
Collapsing on equivalence is safe for coverage reporting: detecting the
representative detects every member.

**Dominance** is asymmetric: fault *g* dominates *f* when every test for *f* is
also a test for *g*. Dropping the dominator shortens the list further, but it is
only safe if the dominated fault is actually targeted and detected. Dominance
collapsing is therefore off by default and flagged in the result, because
reporting a "collapsed fault coverage" that quietly used dominance is not
comparable with published equivalence-collapsed figures.

The default -- equivalence only -- is the convention behind the collapsed fault
counts quoted throughout the ISCAS-85 literature, which is what makes the
numbers this tool prints checkable against them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .circuit import Circuit
from .sim import Fault

#: For each gate kind, (controlling value, output inversion).
#: An input at the controlling value forces the output; inversion says whether
#: the gate negates. XOR and XNOR have no controlling value -- every input
#: matters for every pattern -- so they admit no structural equivalence.
CONTROLLING: dict[str, tuple[int, int]] = {
    "AND": (0, 0),
    "NAND": (0, 1),
    "OR": (1, 0),
    "NOR": (1, 1),
}


def input_line(circuit: Circuit, gate_index: int, pin: int) -> int:
    """Line index feeding `pin` of a gate: the branch if one exists, else the
    stem or primary input line."""
    branch = circuit.branch_of.get((gate_index, pin))
    if branch is not None:
        return branch
    return circuit.line_of_net[circuit.gates[gate_index].inputs[pin]]


def all_faults(circuit: Circuit) -> list[Fault]:
    """The uncollapsed universe: both polarities on every line."""
    return [Fault(ln.index, v) for ln in circuit.lines for v in (0, 1)]


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[tuple[int, int], tuple[int, int]] = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:            # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


@dataclass
class CollapseResult:
    """Outcome of collapsing, with the class map retained.

    `representative` is what makes coverage reporting honest: when a
    representative is detected, every fault in its class is detected too, and
    the map is what lets the tool expand a collapsed result back to the full
    universe rather than quietly reporting collapsed coverage as if it were
    full coverage.
    """

    circuit_name: str
    total: int
    collapsed: list[Fault]
    representative: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)
    used_dominance: bool = False

    @property
    def n_collapsed(self) -> int:
        return len(self.collapsed)

    @property
    def ratio(self) -> float:
        return self.n_collapsed / self.total if self.total else 0.0

    def members(self) -> dict[tuple[int, int], list[tuple[int, int]]]:
        """Representative -> every fault it stands for."""
        out: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for fault, rep in self.representative.items():
            out.setdefault(rep, []).append(fault)
        return out

    def summary(self) -> str:
        mode = "equivalence + dominance" if self.used_dominance else "equivalence"
        return (
            f"{self.circuit_name}: {self.total} faults -> {self.n_collapsed} "
            f"collapsed ({self.ratio:.1%} retained, {mode})"
        )


def collapse(circuit: Circuit, *, dominance: bool = False) -> CollapseResult:
    """Collapse the fault universe of `circuit`."""
    uf = _UnionFind()
    faults = all_faults(circuit)
    for f in faults:
        uf.find((f.line, f.value))

    for gate in circuit.gates:
        out_line = circuit.line_of_net[gate.output]

        if gate.kind in CONTROLLING:
            ctrl, inv = CONTROLLING[gate.kind]
            out_value = ctrl ^ inv
            # Every input stuck at the controlling value forces the same output,
            # and so does the output stuck at that forced value.
            for pin in range(len(gate.inputs)):
                uf.union((out_line, out_value), (input_line(circuit, gate.index, pin), ctrl))

        elif gate.kind == "NOT":
            src = input_line(circuit, gate.index, 0)
            uf.union((out_line, 1), (src, 0))
            uf.union((out_line, 0), (src, 1))

        elif gate.kind == "BUFF":
            src = input_line(circuit, gate.index, 0)
            uf.union((out_line, 0), (src, 0))
            uf.union((out_line, 1), (src, 1))

        # XOR / XNOR: no structural equivalence between input and output faults.

    if dominance:
        _apply_dominance(circuit, uf)

    representative = {(f.line, f.value): uf.find((f.line, f.value)) for f in faults}
    seen: set[tuple[int, int]] = set()
    collapsed: list[Fault] = []
    for f in faults:
        rep = representative[(f.line, f.value)]
        if rep not in seen:
            seen.add(rep)
            collapsed.append(Fault(rep[0], rep[1]))

    return CollapseResult(
        circuit_name=circuit.name,
        total=len(faults),
        collapsed=collapsed,
        representative=representative,
        used_dominance=dominance,
    )


def _apply_dominance(circuit: Circuit, uf: _UnionFind) -> None:
    """Merge dominated faults into their dominators.

    For a gate with a controlling value, the output fault at the *non*-forced
    value is dominated by the corresponding input faults at the non-controlling
    value, provided the gate has a single input. Restricting to unary-like cases
    keeps this sound; the general multi-input dominance relation requires the
    other inputs to be held non-controlling, which is a condition on the test,
    not on the structure.
    """
    for gate in circuit.gates:
        if gate.kind not in CONTROLLING or len(gate.inputs) != 1:
            continue
        ctrl, inv = CONTROLLING[gate.kind]
        out_line = circuit.line_of_net[gate.output]
        src = input_line(circuit, gate.index, 0)
        uf.union((out_line, (1 - ctrl) ^ inv), (src, 1 - ctrl))
