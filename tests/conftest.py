"""Shared fixtures.

The ISCAS-85 benchmarks are fetched rather than vendored (see
`scripts/fetch_benchmarks.py`), so benchmark-dependent tests skip cleanly when
they are absent. The core algorithmic tests use small circuits defined here
instead, so `pytest` is meaningful on a fresh clone with no network.
"""

from pathlib import Path

import pytest

from atpg.circuit import parse_bench

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks"


def bench(name: str):
    path = BENCH_DIR / f"{name}.bench"
    if not path.exists():
        pytest.skip(f"{name}.bench not present; run scripts/fetch_benchmarks.py")
    from atpg.circuit import load_bench
    return load_bench(path)


@pytest.fixture
def c17():
    return bench("c17")


#: A fanout stem feeding two gates that reconverge. Small enough to reason about
#: by hand, and it exercises the stem/branch distinction that several bugs hid in.
RECONVERGENT = """
INPUT(a)
INPUT(b)
INPUT(c)
OUTPUT(z)
n1 = AND(a, b)
n2 = NOT(n1)
n3 = OR(n1, c)
z  = AND(n2, n3)
"""

CHAIN = """
INPUT(a)
INPUT(b)
INPUT(c)
OUTPUT(z)
n1 = NAND(a, b)
n2 = NAND(n1, c)
z  = NOT(n2)
"""

XOR_CIRCUIT = """
INPUT(a)
INPUT(b)
INPUT(c)
OUTPUT(z)
n1 = XOR(a, b)
z  = XOR(n1, c)
"""


@pytest.fixture
def reconvergent():
    return parse_bench(RECONVERGENT, "reconvergent")


@pytest.fixture
def chain():
    return parse_bench(CHAIN, "chain")


@pytest.fixture
def xor_circuit():
    return parse_bench(XOR_CIRCUIT, "xor3")
