"""Bit-parallel combinational logic simulation.

Every net carries a `WORD_BITS`-wide Python integer in which bit *i* holds the
value of that net under pattern *i*. Evaluating a gate is then a single bitwise
operation covering 64 patterns at once. Python's arbitrary-precision integers
make the word width a tunable rather than a hardware limit, but 64 is the sweet
spot: wide enough to amortise interpreter overhead, narrow enough that the
integers stay in the fast small-int path.

This is the same trick a production fault simulator uses, and it is what makes
parallel-pattern fault simulation practical.
"""

from __future__ import annotations

from dataclasses import dataclass

from .circuit import BRANCH, Circuit

WORD_BITS = 64
ALL_ONES = (1 << WORD_BITS) - 1


@dataclass(frozen=True)
class Fault:
    """A single stuck-at fault on a line."""

    line: int          # index into Circuit.lines
    value: int         # 0 or 1: the value the line is stuck at

    def __str__(self) -> str:
        return f"L{self.line}/sa{self.value}"

    def describe(self, circuit: Circuit) -> str:
        return f"{circuit.lines[self.line].name}/sa{self.value}"


def eval_gate(kind: str, words: list[int]) -> int:
    """Evaluate one gate over a packed word of patterns."""
    if kind == "AND":
        out = words[0]
        for w in words[1:]:
            out &= w
        return out
    if kind == "NAND":
        out = words[0]
        for w in words[1:]:
            out &= w
        return ~out & ALL_ONES
    if kind == "OR":
        out = words[0]
        for w in words[1:]:
            out |= w
        return out
    if kind == "NOR":
        out = words[0]
        for w in words[1:]:
            out |= w
        return ~out & ALL_ONES
    if kind == "XOR":
        out = words[0]
        for w in words[1:]:
            out ^= w
        return out
    if kind == "XNOR":
        out = words[0]
        for w in words[1:]:
            out ^= w
        return ~out & ALL_ONES
    if kind == "BUFF":
        return words[0]
    if kind == "NOT":
        return ~words[0] & ALL_ONES
    raise ValueError(f"unsupported gate kind {kind!r}")


class Simulator:
    """Bit-parallel simulator for one circuit.

    A single instance caches the levelised gate order and the branch lookup, so
    repeated simulation -- which is the inner loop of both fault simulation and
    ATPG verification -- avoids re-deriving structure it already knows.
    """

    def __init__(self, circuit: Circuit):
        self.circuit = circuit
        self.order = circuit.gate_order()
        self.has_branches = bool(circuit.branch_of)

    def simulate(
        self,
        input_words: dict[str, int],
        fault: Fault | None = None,
    ) -> dict[str, int]:
        """Simulate, optionally with one stuck-at fault injected.

        Fault injection has to happen in two distinct places, which is the
        subtlety that makes line-based fault modelling worth the effort:

          * a PI or STEM fault forces the net's value everywhere it is used;
          * a BRANCH fault forces the value seen at *one* gate input only,
            leaving the stem and the sibling branches untouched.

        Collapsing these two cases into one is the classic bug: it makes branch
        faults behave like stem faults, which under-reports detectable faults
        and over-reports equivalences.
        """
        circuit = self.circuit
        values = dict(input_words)

        forced_net: str | None = None
        forced_word = 0
        forced_branch: tuple[int, int] | None = None
        if fault is not None:
            line = circuit.lines[fault.line]
            forced_word = ALL_ONES if fault.value else 0
            if line.kind == BRANCH:
                forced_branch = (line.gate, line.pin)
            else:
                forced_net = line.net

        if forced_net is not None and forced_net in values:
            values[forced_net] = forced_word

        for gate in self.order:
            operands = [values[net] for net in gate.inputs]
            if forced_branch is not None and forced_branch[0] == gate.index:
                operands[forced_branch[1]] = forced_word
            out = eval_gate(gate.kind, operands)
            if forced_net == gate.output:
                out = forced_word
            values[gate.output] = out

        return values

    def outputs(self, values: dict[str, int]) -> list[int]:
        return [values[net] for net in self.circuit.outputs]


def pack_patterns(circuit: Circuit, patterns: list[list[int]]) -> dict[str, int]:
    """Pack up to WORD_BITS patterns into one word per primary input.

    `patterns[p][i]` is the value of primary input *i* under pattern *p*.
    """
    if len(patterns) > WORD_BITS:
        raise ValueError(f"at most {WORD_BITS} patterns per pack, got {len(patterns)}")
    words = {net: 0 for net in circuit.inputs}
    for p, pattern in enumerate(patterns):
        if len(pattern) != len(circuit.inputs):
            raise ValueError(
                f"pattern {p} has {len(pattern)} bits, circuit has "
                f"{len(circuit.inputs)} primary inputs"
            )
        bit = 1 << p
        for net, value in zip(circuit.inputs, pattern, strict=True):
            if value:
                words[net] |= bit
    return words


def unpack_outputs(circuit: Circuit, values: dict[str, int], count: int) -> list[list[int]]:
    """Inverse of `pack_patterns` for primary outputs."""
    return [
        [(values[net] >> p) & 1 for net in circuit.outputs]
        for p in range(count)
    ]


def all_input_patterns(circuit: Circuit) -> list[list[int]]:
    """Every input combination. Only sane for small circuits -- it is used to
    establish exhaustive ground truth on c17, where 2^5 = 32 patterns settle
    every question about testability definitively."""
    n = len(circuit.inputs)
    if n > 20:
        raise ValueError(
            f"exhaustive enumeration of {n} inputs is {2 ** n} patterns; "
            "this is intended for small circuits only"
        )
    return [[(p >> i) & 1 for i in range(n)] for p in range(1 << n)]
