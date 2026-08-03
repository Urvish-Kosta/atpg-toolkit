"""atpg-toolkit -- stuck-at fault simulation and test pattern generation.

Four engines that check each other: a reference fault simulator, a bit-parallel
one, PODEM search, and a SAT-based redundancy prover. Redundancy is only ever
claimed on the strength of an UNSAT proof.
"""

from .circuit import Circuit, load_bench, parse_bench
from .faults import collapse
from .flow import run_flow, verify_flow
from .fsim import ParallelFaultSimulator, ReferenceFaultSimulator, random_patterns
from .podem import Podem, Status
from .satatpg import SatAtpg
from .sim import Fault, Simulator

__version__ = "0.1.0"
__all__ = [
    "Circuit", "load_bench", "parse_bench", "collapse", "Fault", "Simulator",
    "ReferenceFaultSimulator", "ParallelFaultSimulator", "random_patterns",
    "Podem", "Status", "SatAtpg", "run_flow", "verify_flow",
]
