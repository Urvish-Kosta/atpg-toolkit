# Security policy

## Scope

atpg-toolkit is a research and educational EDA tool. It parses `.bench` netlists
and runs simulation and search over them. It opens no network connections at
runtime (only `scripts/fetch_benchmarks.py` downloads, and only when invoked),
stores no credentials, and executes no user-supplied code.

`.bench` input is parsed with an explicit grammar and rejected on malformed
input rather than evaluated. Do not extend the parser with `eval` or equivalent.

## The defect class that matters here

The serious failure mode in this tool is not a crash — it is a **false claim of
proven untestability**. A fault reported as redundant when a test actually exists
means a real manufacturing defect ships untested.

If you find a case where the tool reports `REDUNDANT` for a fault that is in fact
testable, please open an issue with the circuit, the fault, and the seed. This is
treated as the highest-severity class of bug in the project. A test pattern
demonstrating detection is ideal, but a reproduction is enough.

False *negatives* in coverage (under-reporting) are less dangerous but still
bugs; please report them too.

## Reporting

Open an issue with a reproducing script. Do not report vulnerabilities in
third-party software here.
