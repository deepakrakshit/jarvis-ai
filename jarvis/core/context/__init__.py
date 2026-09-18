"""JARVIS Context Management & Dynamic Artifact Offloading Subsystem.

Implements the canonical 8-layer context hierarchy (L0-L7), token budgeting,
dynamic Data-Plane artifact offloading, and deterministic pagination.
"""

from jarvis.core.context.builder import ContextBuilder
from jarvis.core.context.compactor import ContextCompactor
from jarvis.core.context.estimator import (
    HeuristicTokenEstimator,
    TiktokenEstimator,
    TokenEstimator,
    create_token_estimator,
)
from jarvis.core.context.offloader import DynamicArtifactOffloader
from jarvis.core.context.pagination import ArtifactPaginator
from jarvis.core.context.schemas import (
    DEFAULT_LAYER_PRIORITY,
    ArtifactReference,
    AssembledContext,
    ContextBudget,
    ContextItem,
    ContextLayerType,
    PaginatedSlice,
    PaginationMetadata,
)

__all__ = [
    "DEFAULT_LAYER_PRIORITY",
    "ArtifactPaginator",
    "ArtifactReference",
    "AssembledContext",
    "ContextBudget",
    "ContextBuilder",
    "ContextCompactor",
    "ContextItem",
    "ContextLayerType",
    "DynamicArtifactOffloader",
    "HeuristicTokenEstimator",
    "PaginatedSlice",
    "PaginationMetadata",
    "TiktokenEstimator",
    "TokenEstimator",
    "create_token_estimator",
]
