"""SAT-based test generation and redundancy proof.

Why this exists
---------------
PODEM is complete in principle: exhausting its search proves a fault untestable.
In practice that guarantee is unreachable for genuinely redundant faults, because
the fallback branches on primary inputs and the space is 2^|PI|. Measured on
c432, c499 and c1355, raising the backtrack limit a hundredfold converts exactly
zero aborted faults into proofs while costing twenty times the runtime. Aborting
is not a tuning problem, it is structural.

A SAT formulation does not have this weakness. Build a *miter*: the good circuit
and the faulty circuit sharing one set of primary inputs, with the outputs
compared. Any satisfying assignment is a test pattern; UNSAT is a proof that no
test exists, i.e. the fault is redundant. Modern CDCL solvers dispatch these
instances easily because conflict learning prunes exactly the structure PODEM's
chronological backtracking rediscovers over and over.

This makes the ABORTED category shrink to almost nothing, which matters: fault
efficiency is only an honest metric if "redundant" means proven.

Encoding
--------
Standard Tseitin encoding, one variable per net per circuit copy. The faulty
copy is only instantiated inside the fault's fanout cone -- outside it the two
circuits are identical by construction, so sharing those variables halves the
formula and costs nothing in completeness.
"""

from __future__ import annotations

from dataclasses import dataclass

from pysat.formula import IDPool
from pysat.solvers import Minisat22

from .circuit import BRANCH, Circuit
from .sim import Fault


@dataclass
class SatResult:
    fault: Fault
    testable: bool
    pattern: list[int] | None
    n_vars: int
    n_clauses: int
    timed_out: bool = False

    @property
    def redundant(self) -> bool:
        """Proven untestable. A timeout is not a proof."""
        return not self.testable and not self.timed_out


def _gate_clauses(kind: str, out: int, ins: list[int]) -> list[list[int]]:
    """Tseitin clauses for one gate.

    NAND/NOR/XNOR are encoded as their positive counterpart driving the negated
    output literal, which keeps the clause tables small and removes four near
    duplicate cases where transcription errors like to hide.
    """
    invert = kind in ("NAND", "NOR", "XNOR")
    base = {"NAND": "AND", "NOR": "OR", "XNOR": "XOR"}.get(kind, kind)
    o = -out if invert else out
    clauses: list[list[int]] = []

    if base == "AND":
        for a in ins:
            clauses.append([-o, a])              # o -> a
        clauses.append([o] + [-a for a in ins])  # all a -> o
    elif base == "OR":
        for a in ins:
            clauses.append([o, -a])              # a -> o
        clauses.append([-o] + list(ins))         # o -> some a
    elif base == "XOR":
        if len(ins) == 1:
            clauses += [[-o, ins[0]], [o, -ins[0]]]
        elif len(ins) == 2:
            a, b = ins
            clauses += [
                [-o, a, b], [-o, -a, -b],
                [o, -a, b], [o, a, -b],
            ]
        else:
            # Wider XORs must be reduced to a chain of two-input gates by
            # _decompose before reaching here; encoding one directly would need
            # 2^n clauses.
            raise ValueError("multi-input XOR must be decomposed before encoding")
    elif base == "BUFF":
        clauses += [[-o, ins[0]], [o, -ins[0]]]
    elif base == "NOT":
        clauses += [[-o, -ins[0]], [o, ins[0]]]
    else:
        raise ValueError(f"unsupported gate kind {kind!r}")
    return clauses


class SatAtpg:
    """Miter-based SAT test generation and redundancy proof."""

    def __init__(self, circuit: Circuit):
        self.circuit = circuit
        self.consumers: dict[str, list[tuple[int, int]]] = {}
        for g in circuit.gates:
            for pin, net in enumerate(g.inputs):
                self.consumers.setdefault(net, []).append((g.index, pin))
        self._cone_cache: dict[int, tuple[set[int], set[str]]] = {}

    def _cone(self, fault: Fault) -> tuple[set[int], set[str]]:
        cached = self._cone_cache.get(fault.line)
        if cached is not None:
            return cached
        line = self.circuit.lines[fault.line]
        start = (self.circuit.gates[line.gate].output if line.kind == BRANCH
                 else line.net)
        gates: set[int] = set()
        nets = {start}
        if line.kind == BRANCH:
            gates.add(line.gate)
        stack = [start]
        while stack:
            net = stack.pop()
            for gate_index, _pin in self.consumers.get(net, ()):
                gates.add(gate_index)
                out = self.circuit.gates[gate_index].output
                if out not in nets:
                    nets.add(out)
                    stack.append(out)
        self._cone_cache[fault.line] = (gates, nets)
        return gates, nets

    def _decompose(self, kind: str, ins: list[int], pool: IDPool,
                   tag: str) -> tuple[str, list[int], list[list[int]]]:
        """Reduce a multi-input XOR/XNOR to a chain of two-input XORs."""
        if kind not in ("XOR", "XNOR") or len(ins) <= 2:
            return kind, ins, []
        clauses: list[list[int]] = []
        acc = ins[0]
        for i, nxt in enumerate(ins[1:-1]):
            tmp = pool.id(f"{tag}_xor{i}")
            clauses += _gate_clauses("XOR", tmp, [acc, nxt])
            acc = tmp
        return kind, [acc, ins[-1]], clauses

    def solve(self, fault: Fault, conflict_budget: int | None = None) -> SatResult:
        """Return a test pattern, or prove the fault redundant.

        `conflict_budget` caps solver effort in CDCL conflicts. On expiry the result is
        neither testable nor proven redundant, so it is reported as UNKNOWN via
        `timed_out` rather than being silently folded into either category --
        an unproven fault counted as redundant inflates fault efficiency, which
        is the exact dishonesty this tool is built to avoid.
        """
        circuit = self.circuit
        line = circuit.lines[fault.line]
        stuck = fault.value
        cone_gates, cone_nets = self._cone(fault)

        pool = IDPool()
        clauses: list[list[int]] = []

        def good(net: str) -> int:
            return pool.id(f"g:{net}")

        def bad(net: str) -> int:
            # Outside the cone the circuits are identical, so share the variable.
            return pool.id(f"b:{net}") if net in cone_nets else good(net)

        # Good circuit.
        for gate in circuit.gate_order():
            kind, ins, extra = self._decompose(
                gate.kind, [good(n) for n in gate.inputs], pool, f"g{gate.index}")
            clauses += extra
            clauses += _gate_clauses(kind, good(gate.output), ins)

        # Faulty circuit, cone only.
        if line.kind != BRANCH:
            # The faulted net is forced to a constant in the faulty copy.
            v = bad(line.net)
            clauses.append([v] if stuck else [-v])

        for gate in circuit.gate_order():
            if gate.index not in cone_gates:
                continue
            if line.kind != BRANCH and gate.output == line.net:
                continue                       # output already constrained
            ins = [bad(n) for n in gate.inputs]
            if line.kind == BRANCH and gate.index == line.gate:
                const = pool.id(f"const{stuck}")
                clauses.append([const] if stuck else [-const])
                ins[line.pin] = const
            kind, ins2, extra = self._decompose(
                gate.kind, ins, pool, f"b{gate.index}")
            clauses += extra
            clauses += _gate_clauses(kind, bad(gate.output), ins2)

        # Miter: require at least one primary output to differ.
        diffs = []
        for net in circuit.outputs:
            if net not in cone_nets:
                continue                       # identical by construction
            d = pool.id(f"d:{net}")
            g, b = good(net), bad(net)
            clauses += [
                [-d, g, b], [-d, -g, -b],      # d -> g != b
                [d, -g, b], [d, g, -b],
            ]
            diffs.append(d)
        if not diffs:
            # The fault cannot reach any primary output at all.
            return SatResult(fault, False, None, pool.top, len(clauses))
        clauses.append(diffs)

        timed_out = False
        with Minisat22(bootstrap_with=clauses) as solver:
            if conflict_budget is None:
                sat = solver.solve()
            else:
                # Deterministic conflict budget rather than a wall-clock
                # interrupt. An earlier version used a threading.Timer calling
                # solver.interrupt(); under load that could return False (UNSAT)
                # instead of None, and the caller then recorded a *timeout as a
                # proof of redundancy*. Sixteen c6288 faults were reported proven
                # untestable that way while a standalone solve on the identical
                # CNF still timed out.
                #
                # A conflict budget cannot be misread: solve_limited returns None
                # when the budget is exhausted, and the result is reproducible
                # run to run, which a wall-clock limit never is.
                solver.conf_budget(int(conflict_budget))
                sat = solver.solve_limited()
                if sat is None:
                    sat, timed_out = False, True
            pattern = None
            if sat:
                model = set(solver.get_model())
                pattern = [1 if good(n) in model else 0 for n in circuit.inputs]

        return SatResult(fault, sat, pattern, pool.top, len(clauses), timed_out)
