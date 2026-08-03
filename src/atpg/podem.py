"""PODEM: Path-Oriented DEcision Making test pattern generation.

Why PODEM rather than the D-algorithm
-------------------------------------
The D-algorithm assigns values to *internal* lines and must then justify them,
which lets it make assignments that are collectively inconsistent and only
discovers this deep in the search. PODEM (Goel, 1981) branches only on *primary
inputs*. Every state it reaches is therefore a legal input assignment by
construction, implication is a single forward simulation, and no justification
step is needed. On circuits with reconvergent fanout -- which is every circuit
anyone cares about -- this is dramatically better behaved.

Value algebra
-------------
Rather than the classical five-valued {0, 1, X, D, D'} with its awkward
multiplication tables, each net carries a *pair* of three-valued signals: the
value in the good circuit and the value in the faulty circuit. D is simply
(1, 0) and D' is (0, 1). Gate evaluation is then plain three-valued logic
applied twice, which is easy to read and hard to get wrong, and it is strictly
more precise than the five-valued algebra when an error meets an unknown --
five-valued logic collapses (X, 0) to X and loses information PODEM could use.

Completeness
------------
PODEM is complete: it branches on both values of each chosen primary input, so
exhausting the search proves a fault untestable (redundant) rather than merely
undiscovered. That guarantee only holds if the search actually runs to
exhaustion, so a fault abandoned at the backtrack limit is reported as ABORTED
and is deliberately *not* counted as redundant. Conflating the two is the most
common way a tool overstates its own fault efficiency.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .circuit import BRANCH, Circuit
from .sim import Fault

X = "X"
ZERO = "0"
ONE = "1"

#: (controlling value, inversion) for gates that have a controlling value.
CONTROL: dict[str, tuple[str, int]] = {
    "AND": (ZERO, 0),
    "NAND": (ZERO, 1),
    "OR": (ONE, 0),
    "NOR": (ONE, 1),
}
INVERTING = {"NAND", "NOR", "NOT", "XNOR"}


class Status(Enum):
    """Outcome for one targeted fault."""

    DETECTED = "detected"
    REDUNDANT = "redundant"      # proven untestable by exhausting the search
    ABORTED = "aborted"          # backtrack limit hit; testability unknown

    def __str__(self) -> str:
        return self.value


def _and3(a: str, b: str) -> str:
    if a == ZERO or b == ZERO:
        return ZERO
    if a == X or b == X:
        return X
    return ONE


def _or3(a: str, b: str) -> str:
    if a == ONE or b == ONE:
        return ONE
    if a == X or b == X:
        return X
    return ZERO


def _xor3(a: str, b: str) -> str:
    if a == X or b == X:
        return X
    return ONE if a != b else ZERO


def _not3(a: str) -> str:
    return X if a == X else (ONE if a == ZERO else ZERO)


def eval3(kind: str, values: list[str]) -> str:
    """Three-valued evaluation of one gate."""
    if kind in ("AND", "NAND"):
        out = values[0]
        for v in values[1:]:
            out = _and3(out, v)
        return _not3(out) if kind == "NAND" else out
    if kind in ("OR", "NOR"):
        out = values[0]
        for v in values[1:]:
            out = _or3(out, v)
        return _not3(out) if kind == "NOR" else out
    if kind in ("XOR", "XNOR"):
        out = values[0]
        for v in values[1:]:
            out = _xor3(out, v)
        return _not3(out) if kind == "XNOR" else out
    if kind == "BUFF":
        return values[0]
    if kind == "NOT":
        return _not3(values[0])
    raise ValueError(f"unsupported gate kind {kind!r}")


@dataclass
class PodemResult:
    fault: Fault
    status: Status
    pattern: list[int] | None = None      # X inputs filled with 0
    decisions: int = 0
    backtracks: int = 0


class Podem:
    """PODEM engine for one circuit. Reusable across faults."""

    def __init__(self, circuit: Circuit, backtrack_limit: int = 2000):
        self.circuit = circuit
        self.limit = backtrack_limit
        self.order = circuit.gate_order()
        self.po = list(circuit.outputs)
        self.pi_set = set(circuit.inputs)
        self.consumers: dict[str, list[tuple[int, int]]] = {}
        for g in circuit.gates:
            for pin, net in enumerate(g.inputs):
                self.consumers.setdefault(net, []).append((g.index, pin))
        self._cone_cache: dict[int, tuple[list, list[str]]] = {}

    def _cone(self, fault: Fault):
        """Gates in the transitive fanout of the fault site, in level order,
        plus the primary outputs reachable from it.

        A single stuck-at fault can only perturb its own fanout cone, so the
        faulty-circuit evaluation, the D-frontier scan, and the detection check
        all restrict to it. On the larger benchmarks the cone is a small
        fraction of the circuit, and scanning all gates on every implication --
        which is what the straightforward implementation does -- is where the
        time goes.
        """
        cached = self._cone_cache.get(fault.line)
        if cached is not None:
            return cached
        line = self.circuit.lines[fault.line]
        start = (self.circuit.gates[line.gate].output if line.kind == BRANCH
                 else line.net)
        seen_gates: set[int] = set()
        stack = [start]
        seen_nets = {start}
        if line.kind == BRANCH:
            seen_gates.add(line.gate)
        while stack:
            net = stack.pop()
            for gate_index, _pin in self.consumers.get(net, ()):
                seen_gates.add(gate_index)
                out = self.circuit.gates[gate_index].output
                if out not in seen_nets:
                    seen_nets.add(out)
                    stack.append(out)
        gates = sorted((self.circuit.gates[i] for i in seen_gates),
                       key=lambda g: g.level)
        pos = [n for n in self.po if n in seen_nets]
        result = (gates, pos)
        self._cone_cache[fault.line] = result
        return result

    # -- implication -------------------------------------------------------
    def _imply(self, assignment: dict[str, str], fault: Fault):
        """Forward-simulate good and faulty circuits from the current PI
        assignment. Returns (good, faulty) value maps."""
        circuit = self.circuit
        line = circuit.lines[fault.line]
        stuck = ONE if fault.value else ZERO

        good: dict[str, str] = {}
        for net in circuit.inputs:
            good[net] = assignment.get(net, X)
        for gate in self.order:
            good[gate.output] = eval3(gate.kind, [good[n] for n in gate.inputs])

        # Outside the cone the faulty circuit is identical to the good one, so
        # `bad` is a thin overlay rather than a second full value map.
        bad = dict(good)
        if line.kind != BRANCH:
            # Inject at the fault site directly. The cone contains the site's
            # *consumers*, not its driver, so relying on the cone loop to force
            # the value works only for branch faults and silently leaves stem
            # faults uninjected.
            bad[line.net] = stuck
        cone_gates, _pos = self._cone(fault)
        for gate in cone_gates:
            operands = [bad[n] for n in gate.inputs]
            if line.kind == BRANCH and gate.index == line.gate:
                operands[line.pin] = stuck
            out = eval3(gate.kind, operands)
            if line.kind != BRANCH and gate.output == line.net:
                out = stuck
            bad[gate.output] = out

        return good, bad

    @staticmethod
    def _error(good: dict[str, str], bad: dict[str, str], net: str) -> bool:
        g, b = good[net], bad[net]
        return g != X and b != X and g != b

    def _detected(self, good, bad, fault: Fault) -> bool:
        _gates, pos = self._cone(fault)
        return any(self._error(good, bad, net) for net in pos)

    # -- objectives --------------------------------------------------------
    def _activated(self, good, bad, fault: Fault) -> bool:
        """Has the fault been excited at its site?

        For a PI or stem fault this is simply an error on the net. A *branch*
        fault is different: its faulty value is injected inside the consuming
        gate, so the stem net itself never shows a discrepancy no matter how the
        inputs are set. Testing for an error on the stem therefore reports every
        branch fault as unexcited forever, and the search then declares testable
        branch faults redundant. Excitation for a branch means the stem carries
        the value opposite to the stuck value.
        """
        line = self.circuit.lines[fault.line]
        stuck = ONE if fault.value else ZERO
        if line.kind == BRANCH:
            net = self.circuit.gates[line.gate].inputs[line.pin]
            return good[net] != X and good[net] != stuck
        return self._error(good, bad, line.net)

    def _d_frontier(self, good, bad, fault: Fault) -> list:
        """Gates with an erroneous input whose output does not yet carry the
        error. These are the places where propagation can still be advanced.

        The gate consuming a faulty *branch* is a special case and must be added
        explicitly. Its faulty value is injected at the gate input rather than
        on the net, so the net shows no error and the ordinary test skips the
        gate entirely -- leaving the frontier empty at the very moment the fault
        has just been excited. PODEM then finds no objective, exhausts nothing,
        and reports a perfectly testable branch fault as redundant.
        """
        out = []
        line = self.circuit.lines[fault.line]
        cone_gates, _pos = self._cone(fault)
        for gate in cone_gates:
            if self._error(good, bad, gate.output):
                continue
            if any(self._error(good, bad, n) for n in gate.inputs):
                out.append(gate)
            elif (line.kind == BRANCH and gate.index == line.gate
                  and self._activated(good, bad, fault)):
                out.append(gate)
        return out

    def _blocked(self, good, bad, net: str) -> bool:
        """Can this net no longer carry an error?

        Only when the good and faulty values are *both* determined and equal.
        The classical five-valued X-path check asks whether the net is X, which
        does not translate to this algebra: a net with good = 1 and faulty = X
        has a determined good value but can still diverge once the remaining
        inputs are assigned. Pruning on `good != X` therefore blocks legitimate
        propagation paths, and PODEM then exhausts its (wrongly truncated)
        search and declares testable faults redundant.
        """
        g, b = good[net], bad[net]
        return g != X and b != X and g == b

    def _x_path_exists(self, good, bad, frontier) -> bool:
        """Is there a path from some D-frontier gate to a primary output along
        which the error is not already blocked?

        Without this check PODEM will happily keep making assignments in a
        region from which the error can no longer escape, and only discover the
        futility by exhausting the whole subtree. It is the single most
        important pruning heuristic in the algorithm.
        """
        if not frontier:
            return False
        po = set(self.po)
        stack = [g.output for g in frontier]
        seen: set[str] = set()
        while stack:
            net = stack.pop()
            if net in seen:
                continue
            seen.add(net)
            if net in po:
                return True
            if self._blocked(good, bad, net):
                continue
            for gate_index, _pin in self.consumers.get(net, ()):
                stack.append(self.circuit.gates[gate_index].output)
        return False

    def _objective(self, good, bad, fault) -> tuple[str, str] | None:
        """Pick a (net, value) goal: activate the fault, else advance the front."""
        line = self.circuit.lines[fault.line]
        target_net = (
            self.circuit.gates[line.gate].inputs[line.pin]
            if line.kind == BRANCH else line.net
        )
        activate = ZERO if fault.value else ONE      # opposite of the stuck value

        if not self._activated(good, bad, fault) and good[target_net] == X:
            return target_net, activate

        frontier = self._d_frontier(good, bad, fault)
        if not frontier:
            return None
        # Scan the whole frontier, not just its first member. Taking only
        # frontier[0] and giving up when that gate has no unassigned input
        # abandons the search while other gates remain advanceable, which shows
        # up as testable faults being reported redundant on circuits with heavy
        # reconvergent fanout -- and not at all on small ones, so it survives
        # any test that only exercises a toy circuit.
        for gate in frontier:
            if gate.kind in CONTROL:
                ctrl, _inv = CONTROL[gate.kind]
                non_ctrl = ONE if ctrl == ZERO else ZERO
            else:
                non_ctrl = ZERO                      # XOR/XNOR: either will do
            for net in gate.inputs:
                if good[net] == X and not self._error(good, bad, net):
                    return net, non_ctrl
        return None

    def _backtrace(self, net: str, value: str, good) -> tuple[str, str]:
        """Walk back from an objective to a primary input, tracking inversions.

        This is a heuristic and is allowed to be wrong: PODEM branches on both
        values of whichever input it selects, so a poor backtrace costs search
        time but never correctness.
        """
        inputs = set(self.circuit.inputs)
        guard = 0
        while net not in inputs:
            guard += 1
            if guard > len(self.circuit.gates) + 1:
                break                                # defensive: malformed graph
            gate = self.circuit.gates[self.circuit.driver[net]]
            if gate.kind in INVERTING:
                value = _not3(value)
            nxt = None
            for candidate in gate.inputs:
                if good[candidate] == X:
                    nxt = candidate
                    break
            if nxt is None:
                break
            net = nxt
        return net, value

    # -- main search -------------------------------------------------------
    def generate(self, fault: Fault) -> PodemResult:
        """Generate a test for `fault`, or prove it redundant."""
        assignment: dict[str, str] = {}
        stats = {"decisions": 0, "backtracks": 0}

        def search() -> bool:
            good, bad = self._imply(assignment, fault)
            if self._detected(good, bad, fault):
                return True
            frontier = self._d_frontier(good, bad, fault)
            line = self.circuit.lines[fault.line]
            target = (
                self.circuit.gates[line.gate].inputs[line.pin]
                if line.kind == BRANCH else line.net
            )
            activated = self._activated(good, bad, fault)
            # If the fault site is already fixed to the stuck value, no test can
            # activate it -- this branch of the search is dead.
            if not activated and good[target] != X:
                return False
            if activated and not self._x_path_exists(good, bad, frontier):
                return False

            objective = self._objective(good, bad, fault)
            if objective is None:
                return False
            pi, value = self._backtrace(*objective, good)

            # Backtrace is a heuristic and can fail to land on an unassigned
            # primary input -- for instance when every input of some gate on the
            # chosen path is already fixed. Abandoning the subtree here would
            # forfeit PODEM's completeness guarantee, and a fault whose search
            # was cut short would then be reported as REDUNDANT rather than
            # merely unsolved. That is a false claim of proven untestability, so
            # fall back to branching on an arbitrary unassigned input instead:
            # correctness of the search does not depend on the choice, only its
            # speed does.
            if pi not in self.pi_set or pi in assignment or good[pi] != X:
                pi = next((n for n in self.circuit.inputs if n not in assignment), None)
                if pi is None:
                    return False
                value = ZERO

            for attempt in (value, _not3(value)):
                if stats["backtracks"] > self.limit:
                    return False
                stats["decisions"] += 1
                assignment[pi] = attempt
                if search():
                    return True
                del assignment[pi]
                stats["backtracks"] += 1
            return False

        found = search()
        if found:
            pattern = [1 if assignment.get(n, ZERO) == ONE else 0
                       for n in self.circuit.inputs]
            status = Status.DETECTED
        else:
            pattern = None
            status = (Status.ABORTED if stats["backtracks"] > self.limit
                      else Status.REDUNDANT)

        return PodemResult(fault, status, pattern,
                           stats["decisions"], stats["backtracks"])
