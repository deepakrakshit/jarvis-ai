"""JARVIS Memory Plane and Context Engine.

Implements Sections 25 and 26 of the authoritative architecture:
- Working, Episodic, Semantic, User, and Project memory stores.
- Hybrid FTS5 and semantic retrieval.
- Maximal Marginal Relevance (MMR) and Temporal Decay ranking.
- Security write pipeline with credential redaction and provenance gating.
- Prospective memory (Standing Intents).
- Model-aware, token-budgeted context assembly.
"""

from jarvis.memory.context_engine import ContextEngine, context_engine
from jarvis.memory.governance import MemoryGovernance
from jarvis.memory.manager import MemoryManager, memory_manager
from jarvis.memory.ranking import apply_temporal_decay, jaccard_similarity, mmr_rerank, tokenize
from jarvis.memory.standing_intents import StandingIntent, StandingIntentManager
from jarvis.memory.working import InteractionTurn, WorkingMemory

__all__ = [
    "ContextEngine",
    "context_engine",
    "MemoryGovernance",
    "MemoryManager",
    "memory_manager",
    "WorkingMemory",
    "InteractionTurn",
    "StandingIntent",
    "StandingIntentManager",
    "apply_temporal_decay",
    "mmr_rerank",
    "tokenize",
    "jaccard_similarity",
]
