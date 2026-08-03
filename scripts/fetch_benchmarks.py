#!/usr/bin/env python3
"""Download the ISCAS-85 benchmark circuits.

The circuits are not vendored in this repository. They are third-party academic
benchmarks first distributed with Brglez and Fujiwara's 1985 ISCAS paper, and
the right thing to do with third-party material is to fetch it with its
provenance recorded rather than commit a copy with no attribution.

    F. Brglez and H. Fujiwara, "A Neutral Netlist of 10 Combinational Benchmark
    Circuits and a Target Translator in Fortran", Proc. IEEE Int. Symposium on
    Circuits and Systems (ISCAS), 1985.

Usage:
    python scripts/fetch_benchmarks.py
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://pld.ttu.ee/~maksim/benchmarks/iscas85/bench"
CIRCUITS = [
    "c17", "c432", "c499", "c880", "c1355", "c1908",
    "c2670", "c3540", "c5315", "c6288", "c7552",
]

#: Line counts double as a checksum: each ISCAS-85 circuit is named after the
#: number of lines (fault sites) it contains, so a parsed circuit whose line
#: count does not match its name was mis-parsed or mis-downloaded.
EXPECTED_GATES = {
    "c17": 6, "c432": 160, "c499": 202, "c880": 383, "c1355": 546,
    "c1908": 880, "c2670": 1193, "c3540": 1669, "c5315": 2307,
    "c6288": 2416, "c7552": 3512,
}

OUT = Path(__file__).resolve().parents[1] / "benchmarks"


def main() -> int:
    OUT.mkdir(exist_ok=True)
    failures = []
    for name in CIRCUITS:
        target = OUT / f"{name}.bench"
        if target.exists():
            print(f"  {name:8s} already present")
            continue
        url = f"{BASE}/{name}.bench"
        try:
            with urllib.request.urlopen(url, timeout=60) as fh:
                data = fh.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  {name:8s} FAILED: {exc}")
            failures.append(name)
            continue
        target.write_bytes(data)
        print(f"  {name:8s} {len(data):>7,d} bytes")

    print("\nverifying...")
    sys.path.insert(0, str(OUT.parent / "src"))
    from atpg.circuit import load_bench

    bad = 0
    for name in CIRCUITS:
        path = OUT / f"{name}.bench"
        if not path.exists():
            continue
        c = load_bench(path)
        expected_lines = int(name[1:])
        ok = c.n_lines == expected_lines and c.n_gates == EXPECTED_GATES[name]
        bad += not ok
        print(f"  {name:8s} {c.n_gates:5d} gates, {c.n_lines:5d} lines  "
              f"{'ok' if ok else 'MISMATCH'}")

    if failures or bad:
        print(f"\n{len(failures)} download failure(s), {bad} verification failure(s)")
        return 1
    print("\nall circuits downloaded and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
