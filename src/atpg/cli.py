"""Command line interface for atpg-toolkit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .circuit import load_bench
from .faults import collapse
from .flow import run_flow, verify_flow
from .fsim import ParallelFaultSimulator, ReferenceFaultSimulator, random_patterns
from .satatpg import SatAtpg

BENCH_DIR = Path(__file__).resolve().parents[2] / "benchmarks"


def _resolve(name: str) -> Path:
    path = Path(name)
    if path.exists():
        return path
    candidate = BENCH_DIR / (name if name.endswith(".bench") else f"{name}.bench")
    if candidate.exists():
        return candidate
    raise SystemExit(
        f"circuit {name!r} not found. Place a .bench file at that path, or run "
        "scripts/fetch_benchmarks.py to download the ISCAS-85 suite."
    )


def cmd_info(args) -> int:
    c = load_bench(_resolve(args.circuit))
    r = collapse(c, dominance=args.dominance)
    print(c.summary())
    print(r.summary())
    return 0


def cmd_fsim(args) -> int:
    c = load_bench(_resolve(args.circuit))
    faults = collapse(c).collapsed
    pats = random_patterns(c, args.patterns, seed=args.seed)
    engine = (ReferenceFaultSimulator if args.reference else ParallelFaultSimulator)(c)
    result = engine.run(pats, faults)
    print(result.summary())
    if args.cross_check:
        other = ReferenceFaultSimulator(c).run(pats, faults)
        agree = other.detected == result.detected
        print(f"cross-check against reference simulator: "
              f"{'AGREE' if agree else 'DISAGREE'}")
        return 0 if agree else 1
    return 0


def cmd_atpg(args) -> int:
    c = load_bench(_resolve(args.circuit))
    result = run_flow(
        c,
        n_random=args.random,
        backtrack_limit=args.backtracks,
        seed=args.seed,
        do_compact=not args.no_compact,
        use_sat=not args.no_sat,
        sat_budget=args.sat_budget,
    )
    print(result.summary())
    ok, recomputed = verify_flow(c, result)
    print(f"  independent re-simulation of the delivered patterns: "
          f"{'CONFIRMED' if ok else 'MISMATCH'} ({recomputed} detected)")
    if args.write:
        Path(args.write).write_text(
            "".join("".join(map(str, p)) + "\n" for p in result.patterns)
        )
        print(f"  wrote {len(result.patterns)} patterns to {args.write}")
    return 0 if ok else 1


def cmd_redundancy(args) -> int:
    c = load_bench(_resolve(args.circuit))
    faults = collapse(c).collapsed
    engine = SatAtpg(c)
    testable = redundant = unknown = 0
    for f in faults:
        r = engine.solve(f, conflict_budget=args.sat_budget)
        testable += r.testable
        redundant += r.redundant
        unknown += r.timed_out
    print(f"{c.name}: {len(faults)} collapsed faults")
    print(f"  testable   : {testable}")
    print(f"  redundant  : {redundant}  (proven untestable by UNSAT)")
    print(f"  unknown    : {unknown}  (conflict budget exhausted; NOT a proof)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="atpg",
        description="Stuck-at fault simulation and test pattern generation "
                    "for combinational circuits.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("info", help="circuit and fault-list statistics")
    sp.add_argument("circuit")
    sp.add_argument("--dominance", action="store_true",
                    help="also collapse on dominance (not comparable with "
                         "published equivalence-collapsed counts)")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("fsim", help="fault simulation with random patterns")
    sp.add_argument("circuit")
    sp.add_argument("-n", "--patterns", type=int, default=1024)
    sp.add_argument("--seed", type=int, default=1)
    sp.add_argument("--reference", action="store_true",
                    help="use the slow reference engine")
    sp.add_argument("--cross-check", action="store_true",
                    help="run both engines and require agreement")
    sp.set_defaults(func=cmd_fsim)

    sp = sub.add_parser("atpg", help="full flow: random, PODEM, then SAT")
    sp.add_argument("circuit")
    sp.add_argument("-r", "--random", type=int, default=2048)
    sp.add_argument("-b", "--backtracks", type=int, default=50)
    sp.add_argument("--seed", type=int, default=1)
    sp.add_argument("--sat-budget", type=int, default=200_000,
                    help="CDCL conflict budget per fault")
    sp.add_argument("--no-sat", action="store_true")
    sp.add_argument("--no-compact", action="store_true")
    sp.add_argument("--write", metavar="FILE", help="write the pattern set")
    sp.set_defaults(func=cmd_atpg)

    sp = sub.add_parser("redundancy", help="SAT proof of untestability per fault")
    sp.add_argument("circuit")
    sp.add_argument("--sat-budget", type=int, default=200_000)
    sp.set_defaults(func=cmd_redundancy)

    return p


def main(argv=None) -> int:
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
