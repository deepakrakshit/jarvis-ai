"""JARVIS Continuous Evaluation and Trajectory Scoring Subsystem (Milestone 13).

Provides the 6-Level Test Matrix and Multiplicative Security Gating:
Score = Gate_Policy * Gate_Approval * Gate_Verify * Execution_Quality
"""

from jarvis.evals.runner import EvaluationRunner, get_canonical_benchmark_cases
from jarvis.evals.schemas import (
    EvaluationCase,
    EvaluationLevel,
    EvaluationReport,
    EvaluationResult,
    MultiplicativeGateResult,
    TrajectoryStep,
)
from jarvis.evals.scoring import TrajectoryScorer

__all__ = [
    "EvaluationCase",
    "EvaluationLevel",
    "EvaluationReport",
    "EvaluationResult",
    "EvaluationRunner",
    "MultiplicativeGateResult",
    "TrajectoryScorer",
    "TrajectoryStep",
    "get_canonical_benchmark_cases",
]
