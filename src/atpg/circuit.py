"""Combinational circuit representation and ISCAS `.bench` parsing.

Design note: lines, not gates
-----------------------------
Stuck-at faults live on *lines*, not on gates, and the distinction matters. Where
a net fans out to several gates, the stem and each branch are separate fault
sites: a fault on one branch is invisible on the others, so collapsing them into
a single site under-counts the fault universe and silently inflates the reported
coverage.

This module therefore models three kinds of line explicitly:

    PI     a primary input
    STEM   a gate output (the driver side of a net)
    BRANCH (net, consumer gate, pin) -- one per fanout when fanout > 1

A net driving exactly one gate has no branch lines; stem and branch would be
electrically and logically identical, so adding both would double-count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: Gate types supported by the ISCAS `.bench` format. BUFF and NOT are unary;
#: the rest take two or more inputs.
GATE_KINDS = {"AND", "NAND", "OR", "NOR", "XOR", "XNOR", "NOT", "BUFF"}
UNARY = {"NOT", "BUFF"}

PI = "PI"
STEM = "STEM"
BRANCH = "BRANCH"


class BenchParseError(ValueError):
    """Raised on malformed or unsupported `.bench` input."""


@dataclass
class Gate:
    """One combinational gate."""

    index: int
    kind: str
    output: str
    inputs: list[str]
    level: int = 0


@dataclass(frozen=True)
class Line:
    """A fault site.

    `gate` and `pin` are set only for BRANCH lines and identify the consuming
    gate input; for PI and STEM lines they are None.
    """

    index: int
    kind: str
    net: str
    gate: int | None = None
    pin: int | None = None

    @property
    def name(self) -> str:
        if self.kind == BRANCH:
            return f"{self.net}->g{self.gate}.{self.pin}"
        return self.net

    def __str__(self) -> str:
        return self.name


@dataclass
class Circuit:
    """A levelised combinational circuit."""

    name: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    gates: list[Gate] = field(default_factory=list)
    driver: dict[str, int] = field(default_factory=dict)      # net -> gate index
    fanout: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    lines: list[Line] = field(default_factory=list)
    #: (gate index, pin) -> line index, for branch fault injection
    branch_of: dict[tuple[int, int], int] = field(default_factory=dict)
    #: net -> line index of its stem or PI line
    line_of_net: dict[str, int] = field(default_factory=dict)
    max_level: int = 0

    # -- structural queries -------------------------------------------------
    @property
    def n_gates(self) -> int:
        return len(self.gates)

    @property
    def n_lines(self) -> int:
        return len(self.lines)

    def gate_order(self) -> list[Gate]:
        """Gates in topological (level) order."""
        return sorted(self.gates, key=lambda g: g.level)

    def summary(self) -> str:
        branches = sum(1 for ln in self.lines if ln.kind == BRANCH)
        return (
            f"{self.name}: {len(self.inputs)} PI, {len(self.outputs)} PO, "
            f"{self.n_gates} gates, depth {self.max_level}, "
            f"{self.n_lines} lines ({branches} fanout branches)"
        )


_INPUT_RE = re.compile(r"^\s*INPUT\s*\(\s*([^)\s]+)\s*\)", re.I)
_OUTPUT_RE = re.compile(r"^\s*OUTPUT\s*\(\s*([^)\s]+)\s*\)", re.I)
_GATE_RE = re.compile(r"^\s*([^\s=]+)\s*=\s*([A-Za-z]+)\s*\(([^)]*)\)")


def parse_bench(text: str, name: str = "circuit") -> Circuit:
    """Parse ISCAS `.bench` source into a `Circuit`."""
    circuit = Circuit(name=name)
    seen_outputs: set[str] = set()

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#")[0].strip()
        if not line:
            continue

        m = _INPUT_RE.match(line)
        if m:
            circuit.inputs.append(m.group(1))
            continue
        m = _OUTPUT_RE.match(line)
        if m:
            circuit.outputs.append(m.group(1))
            continue
        m = _GATE_RE.match(line)
        if not m:
            raise BenchParseError(f"line {lineno}: cannot parse {raw.strip()!r}")

        out, kind, args = m.group(1), m.group(2).upper(), m.group(3)
        if kind == "DFF":
            raise BenchParseError(
                f"line {lineno}: sequential circuits (DFF) are not supported; "
                "this tool targets combinational ISCAS-85 style netlists"
            )
        if kind not in GATE_KINDS:
            raise BenchParseError(f"line {lineno}: unsupported gate type {kind!r}")
        ins = [a.strip() for a in args.split(",") if a.strip()]
        if not ins:
            raise BenchParseError(f"line {lineno}: gate {out} has no inputs")
        if kind in UNARY and len(ins) != 1:
            raise BenchParseError(f"line {lineno}: {kind} takes exactly one input")
        if out in seen_outputs or out in circuit.inputs:
            raise BenchParseError(f"line {lineno}: net {out!r} driven more than once")
        seen_outputs.add(out)

        circuit.driver[out] = len(circuit.gates)
        circuit.gates.append(Gate(len(circuit.gates), kind, out, ins))

    _validate(circuit)
    _levelise(circuit)
    _build_lines(circuit)
    return circuit


def _validate(circuit: Circuit) -> None:
    known = set(circuit.inputs) | set(circuit.driver)
    for g in circuit.gates:
        for net in g.inputs:
            if net not in known:
                raise BenchParseError(f"gate {g.output}: undriven net {net!r}")
    for net in circuit.outputs:
        if net not in known:
            raise BenchParseError(f"primary output {net!r} is not driven")
    if not circuit.inputs:
        raise BenchParseError("circuit has no primary inputs")
    if not circuit.outputs:
        raise BenchParseError("circuit has no primary outputs")


def _levelise(circuit: Circuit) -> None:
    """Assign each gate a level = 1 + max(level of its inputs).

    Iterative rather than recursive: c7552 is deep enough that a naive recursive
    depth-first levelisation risks exceeding Python's stack limit, and a tool
    that works on nine circuits and crashes on the tenth is not a tool.
    """
    level: dict[str, int] = {net: 0 for net in circuit.inputs}
    remaining = {g.index for g in circuit.gates}

    # Kahn-style: repeatedly emit gates whose inputs are all resolved.
    pending_count = {g.index: sum(1 for n in g.inputs if n not in level)
                     for g in circuit.gates}
    ready = [i for i in remaining if pending_count[i] == 0]
    waiting: dict[str, list[int]] = {}
    for g in circuit.gates:
        for net in g.inputs:
            if net not in level:
                waiting.setdefault(net, []).append(g.index)

    while ready:
        idx = ready.pop()
        g = circuit.gates[idx]
        g.level = 1 + max(level[n] for n in g.inputs)
        level[g.output] = g.level
        circuit.max_level = max(circuit.max_level, g.level)
        remaining.discard(idx)
        for consumer in waiting.get(g.output, ()):
            pending_count[consumer] -= 1
            if pending_count[consumer] == 0:
                ready.append(consumer)

    if remaining:
        stuck = [circuit.gates[i].output for i in sorted(remaining)][:5]
        raise BenchParseError(
            f"combinational loop detected; {len(remaining)} gates unresolved, "
            f"e.g. {stuck}"
        )


def _build_lines(circuit: Circuit) -> None:
    """Enumerate fault sites and record the fanout structure."""
    for g in circuit.gates:
        for pin, net in enumerate(g.inputs):
            circuit.fanout.setdefault(net, []).append((g.index, pin))
    for net in circuit.outputs:
        circuit.fanout.setdefault(net, [])

    for net in circuit.inputs:
        circuit.line_of_net[net] = len(circuit.lines)
        circuit.lines.append(Line(len(circuit.lines), PI, net))
    for g in circuit.gates:
        circuit.line_of_net[g.output] = len(circuit.lines)
        circuit.lines.append(Line(len(circuit.lines), STEM, g.output))

    # Branch lines only where a net actually fans out to more than one place.
    # A primary output counts as a destination: a net that feeds one gate and
    # also leaves the circuit is a genuine fanout point.
    po = set(circuit.outputs)
    for net, consumers in circuit.fanout.items():
        degree = len(consumers) + (1 if net in po else 0)
        if degree <= 1:
            continue
        for gate_index, pin in consumers:
            circuit.branch_of[(gate_index, pin)] = len(circuit.lines)
            circuit.lines.append(
                Line(len(circuit.lines), BRANCH, net, gate_index, pin)
            )


def load_bench(path: str | Path) -> Circuit:
    """Parse a `.bench` file, taking the circuit name from the filename."""
    path = Path(path)
    return parse_bench(path.read_text(), name=path.stem)
