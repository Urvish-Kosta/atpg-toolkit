"""Fault simulation: two independent engines that must agree.

`ReferenceFaultSimulator` is deliberately naive. For every fault it re-simulates
the entire circuit from scratch and compares every primary output. It is slow
and obviously correct, and it is the executable specification.

`ParallelFaultSimulator` is the one you would actually use. It exploits three
standard optimisations, each of which is a chance to be subtly wrong:

  * **bit-parallel patterns** -- 64 patterns per pass, already provided by `sim`;
  * **event-driven cone propagation** -- only re-evaluate gates whose inputs
    actually changed, in level order, and abandon the fault the moment the
    divergence dies out before reaching an output;
  * **fault dropping** -- once a fault is detected it leaves the active list.

Agreement between the two on the *set of detected faults* is the correctness
argument for the fast one. This mirrors the differential approach used against a
reference core: an optimised implementation is trustworthy exactly to the extent
that it reproduces a simple one it shares no code with.

Note that the two need not agree on *which pattern* first detects a fault --
fault dropping changes the order in which work happens. They must agree on the
detected set, which is the quantity coverage is computed from.
"""

from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field

from .circuit import BRANCH, Circuit
from .sim import ALL_ONES, Fault, Simulator, eval_gate, pack_patterns


@dataclass
class FaultSimResult:
    """Outcome of simulating a pattern set against a fault list."""

    circuit_name: str
    n_faults: int
    detected: set[tuple[int, int]] = field(default_factory=set)
    n_patterns: int = 0
    #: fault -> index of the pattern that first detected it
    first_detection: dict[tuple[int, int], int] = field(default_factory=dict)

    @property
    def n_detected(self) -> int:
        return len(self.detected)

    @property
    def coverage(self) -> float:
        return self.n_detected / self.n_faults if self.n_faults else 0.0

    def undetected(self, faults) -> list[Fault]:
        return [f for f in faults if (f.line, f.value) not in self.detected]

    def summary(self) -> str:
        return (
            f"{self.circuit_name}: {self.n_detected}/{self.n_faults} faults "
            f"detected ({self.coverage:.2%}) by {self.n_patterns} patterns"
        )


class ReferenceFaultSimulator:
    """Executable specification. Correct by inspection, slow by design."""

    def __init__(self, circuit: Circuit):
        self.circuit = circuit
        self.sim = Simulator(circuit)

    def run(self, patterns: list[list[int]], faults: list[Fault]) -> FaultSimResult:
        circuit = self.circuit
        result = FaultSimResult(circuit.name, len(faults), n_patterns=len(patterns))

        for base in range(0, len(patterns), 64):
            chunk = patterns[base:base + 64]
            words = pack_patterns(circuit, chunk)
            good = self.sim.simulate(words)
            good_po = [good[net] for net in circuit.outputs]

            for f in faults:
                key = (f.line, f.value)
                if key in result.detected:
                    continue
                bad = self.sim.simulate(words, f)
                diff = 0
                for net, g in zip(circuit.outputs, good_po, strict=True):
                    diff |= bad[net] ^ g
                if diff:
                    result.detected.add(key)
                    result.first_detection[key] = base + (diff & -diff).bit_length() - 1
        return result


class ParallelFaultSimulator:
    """Event-driven, fault-dropping, bit-parallel fault simulator."""

    def __init__(self, circuit: Circuit):
        self.circuit = circuit
        self.sim = Simulator(circuit)
        self.po = set(circuit.outputs)
        # net -> consumers, including gate level, precomputed once.
        self.consumers: dict[str, list[tuple[int, int, int]]] = {}
        for gate in circuit.gates:
            for pin, net in enumerate(gate.inputs):
                self.consumers.setdefault(net, []).append((gate.level, gate.index, pin))

    def _propagate(self, good: dict[str, int], fault: Fault, active: int) -> int:
        """Return the mask of patterns for which `fault` reaches a primary output.

        `active` masks the patterns still worth considering. Everything outside
        the fault's cone keeps its good value, so only diverging nets are stored.
        """
        circuit = self.circuit
        line = circuit.lines[fault.line]
        forced = ALL_ONES if fault.value else 0

        diff: dict[str, int] = {}          # net -> faulty value, where different
        queue: list[tuple[int, int]] = []
        seen: set[int] = set()
        detected = 0

        def schedule(net: str) -> None:
            for level, gate_index, _pin in self.consumers.get(net, ()):
                if gate_index not in seen:
                    seen.add(gate_index)
                    heapq.heappush(queue, (level, gate_index))

        def record(net: str, faulty: int) -> int:
            """Detection must be tested wherever a divergence appears, including
            at the seeding site. A net can be both an internal signal and a
            primary output; checking only inside the propagation loop misses
            every fault whose effect surfaces directly on such a net, and does so
            silently -- it under-reports coverage rather than crashing."""
            if net in self.po:
                return (faulty ^ good[net]) & active
            return 0

        if line.kind == BRANCH:
            # A branch fault perturbs exactly one gate input. The stem and the
            # sibling branches keep their good values, so nothing else is
            # scheduled and the divergence starts at this gate alone.
            gate = circuit.gates[line.gate]
            operands = [good[n] for n in gate.inputs]
            operands[line.pin] = forced
            out = eval_gate(gate.kind, operands)
            if out == good[gate.output]:
                return 0
            diff[gate.output] = out
            seen.add(gate.index)
            detected |= record(gate.output, out)
            schedule(gate.output)
        else:
            if good[line.net] == forced:
                return 0                  # fault site already at the stuck value
            diff[line.net] = forced
            detected |= record(line.net, forced)
            schedule(line.net)

        while queue:
            _level, gate_index = heapq.heappop(queue)
            seen.discard(gate_index)
            gate = circuit.gates[gate_index]
            operands = [diff.get(n, good[n]) for n in gate.inputs]
            if line.kind == BRANCH and gate_index == line.gate:
                operands[line.pin] = forced
            out = eval_gate(gate.kind, operands)
            if out == good[gate.output]:
                # Divergence died here. Any previously recorded difference on
                # this net must be withdrawn, or later gates would see a stale
                # faulty value -- a real source of false detections.
                if diff.pop(gate.output, None) is not None:
                    schedule(gate.output)
                continue
            if diff.get(gate.output) == out:
                continue                  # no change, no need to re-propagate
            diff[gate.output] = out
            if gate.output in self.po:
                detected |= (out ^ good[gate.output]) & active
            schedule(gate.output)

        return detected

    def run(
        self,
        patterns: list[list[int]],
        faults: list[Fault],
        *,
        drop: bool = True,
    ) -> FaultSimResult:
        circuit = self.circuit
        result = FaultSimResult(circuit.name, len(faults), n_patterns=len(patterns))
        active = list(faults)

        for base in range(0, len(patterns), 64):
            chunk = patterns[base:base + 64]
            mask = (1 << len(chunk)) - 1
            words = pack_patterns(circuit, chunk)
            good = self.sim.simulate(words)

            still: list[Fault] = []
            for f in active:
                hit = self._propagate(good, f, mask)
                if hit:
                    key = (f.line, f.value)
                    result.detected.add(key)
                    result.first_detection.setdefault(
                        key, base + (hit & -hit).bit_length() - 1
                    )
                    if drop:
                        continue
                still.append(f)
            active = still
            if drop and not active:
                break

        return result


def random_patterns(circuit: Circuit, count: int, seed: int = 0) -> list[list[int]]:
    """Uniform random input patterns. The standard first stage of a test flow:
    random patterns are nearly free and knock out the easy faults, leaving ATPG
    to spend its effort on the hard ones."""
    rng = random.Random(seed)
    n = len(circuit.inputs)
    return [[rng.getrandbits(1) for _ in range(n)] for _ in range(count)]
