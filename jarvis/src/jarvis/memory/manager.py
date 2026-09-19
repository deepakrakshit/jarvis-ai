"""Unified Memory Manager for JARVIS Memory Plane.

Provides the comprehensive interface uniting:
- Long-term persistent store (Episodic, Semantic, User Preferences, Project, Device).
- Full-text search (FTS5) and keyword retrieval.
- Temporal decay and Maximal Marginal Relevance (MMR) ranking.
- Security governance and sensitive data redaction.
- Working memory caching per active session.
- Prospective memory (Standing Intents).
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from jarvis.contracts.memory import MemoryRecord, MemoryType
from jarvis.memory.governance import MemoryGovernance
from jarvis.memory.ranking import apply_temporal_decay, mmr_rerank
from jarvis.memory.standing_intents import StandingIntent, StandingIntentManager
from jarvis.memory.working import WorkingMemory
from jarvis.storage.database import DatabaseEngine, db


class MemoryManager:
    """Master manager for JARVIS working and long-term memory systems."""

    def __init__(self, database: Optional[DatabaseEngine] = None) -> None:
        self.db = database or db
        self.governance = MemoryGovernance(redact_secrets=True)
        self.intents = StandingIntentManager(database=self.db)
        self._working_cache: Dict[str, WorkingMemory] = {}

    def store(self, record: MemoryRecord) -> MemoryRecord:
        """Admit, sanitize, and persist a memory record."""
        admitted_record = self.governance.evaluate_write_admission(record)
        self.db.save_memory(admitted_record)
        return admitted_record

    def search(
        self,
        query: str,
        memory_type: Optional[MemoryType] = None,
        limit: int = 10,
        enable_mmr: bool = True,
        lambda_param: float = 0.7,
        enable_temporal_decay: bool = True,
        half_life_days: float = 30.0,
    ) -> List[MemoryRecord]:
        """Search memories using hybrid FTS, temporal decay, and MMR diversity re-ranking.

        Args:
            query: Search query terms.
            memory_type: Optional type filter.
            limit: Maximum records to return.
            enable_mmr: Whether to re-rank using Maximal Marginal Relevance.
            lambda_param: Weighting for MMR (0 = max diversity, 1 = max relevance).
            enable_temporal_decay: Whether to decay old memories.
            half_life_days: Half-life period for temporal decay.
        """
        # Fetch candidate pool (3x limit for diverse re-ranking)
        candidates = self.db.search_memories_fts(
            query=query,
            memory_type=memory_type,
            limit=max(limit * 3, 20),
        )

        if not candidates:
            return []

        now = datetime.now(timezone.utc)
        scored_candidates: List[MemoryRecord] = []

        # Step 1: Apply temporal decay
        for candidate in candidates:
            score = candidate.importance_score
            if enable_temporal_decay:
                is_evergreen = candidate.memory_type in {
                    MemoryType.USER_PREFERENCE,
                    MemoryType.PROJECT,
                    MemoryType.DEVICE_STATE,
                }
                age_days = (now - candidate.updated_at).total_seconds() / 86400.0
                score = apply_temporal_decay(
                    score=score,
                    age_days=age_days,
                    half_life_days=half_life_days,
                    is_evergreen=is_evergreen,
                )
            # Update score for ranking
            candidate.importance_score = score
            scored_candidates.append(candidate)

        # Step 2: Apply MMR Re-Ranking if requested
        if enable_mmr and len(scored_candidates) > 1:
            return mmr_rerank(
                items=scored_candidates,
                score_fn=lambda rec: rec.importance_score,
                snippet_fn=lambda rec: f"{rec.key} {rec.content}",
                lambda_param=lambda_param,
                limit=limit,
            )

        # Otherwise sort purely by score
        scored_candidates.sort(key=lambda rec: rec.importance_score, reverse=True)
        return scored_candidates[:limit]

    def get_working_memory(self, session_id: str) -> WorkingMemory:
        """Get or initialize the in-memory working context for a session."""
        if session_id not in self._working_cache:
            self._working_cache[session_id] = WorkingMemory(session_id=session_id)
        return self._working_cache[session_id]

    def clear_working_memory(self, session_id: str) -> None:
        """Clear the working context for a session."""
        if session_id in self._working_cache:
            del self._working_cache[session_id]

    # Standing Intent Convenience
    def register_standing_intent(
        self,
        description: str,
        trigger_keywords: List[str],
        session_id: Optional[str] = None,
        max_fires: int = 3,
        cooldown_seconds: int = 86400,
    ) -> StandingIntent:
        """Register a new prospective intent."""
        intent = StandingIntent(
            description=description,
            trigger_keywords=trigger_keywords,
            session_id=session_id,
            max_fires=max_fires,
            cooldown_seconds=cooldown_seconds,
        )
        return self.intents.register(intent)

    def evaluate_standing_intents(
        self, text: str, session_id: Optional[str] = None
    ) -> List[StandingIntent]:
        """Evaluate event or input against prospective intents."""
        return self.intents.evaluate(text, session_id=session_id)


# Default singleton instance
memory_manager = MemoryManager()
