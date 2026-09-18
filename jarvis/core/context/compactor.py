"""JARVIS Context Compaction and Summarization Subsystem.

Provides progressive summarization of older dialogue turns and observations when
approaching token thresholds, strictly preserving Information Flow Control (IFC)
labels and artifact reference provenance.
"""

from jarvis.core.context.estimator import TokenEstimator, create_token_estimator
from jarvis.core.context.schemas import ContextItem, ContextLayerType
from jarvis.core.trust.taxonomy import TrustLevel, meet_trust_levels


class ContextCompactor:
    """Progressive context compactor with strict trust label preservation."""

    def __init__(
        self,
        estimator: TokenEstimator | None = None,
        keep_recent_turns: int = 4,
    ) -> None:
        self.estimator = estimator or create_token_estimator()
        self.keep_recent_turns = max(1, keep_recent_turns)

    def compact_dialogue(
        self,
        items: list[ContextItem],
        target_token_reduction: int | None = None,
    ) -> tuple[list[ContextItem], bool]:
        """Compact dialogue history items (L5), keeping recent turns intact.

        Returns (compacted_items, was_compacted).
        """
        dialogue_items = [it for it in items if it.layer == ContextLayerType.L5_DIALOGUE_HISTORY]
        if len(dialogue_items) <= self.keep_recent_turns:
            return items, False

        older_items = dialogue_items[: -self.keep_recent_turns]
        recent_items = dialogue_items[-self.keep_recent_turns :]

        # Determine aggregate trust level across older items (IFC Non-Elevation Invariant)
        aggregate_trust = TrustLevel.SYSTEM_POLICY
        for it in older_items:
            aggregate_trust = meet_trust_levels(aggregate_trust, it.trust_level)

        # Collect referenced artifacts from metadata
        referenced_artifacts: list[str] = []
        for it in older_items:
            if "artifact_id" in it.metadata:
                referenced_artifacts.append(it.metadata["artifact_id"])
            if "artifacts" in it.metadata and isinstance(it.metadata["artifacts"], list):
                referenced_artifacts.extend(it.metadata["artifacts"])

        # Build compact extractive summary of older dialogue
        summary_lines: list[str] = ["[Compacted Prior Dialogue Summary]"]
        for idx, it in enumerate(older_items, 1):
            role = it.metadata.get("role", "speaker")
            # Truncate each older turn to a concise 1-2 line summary
            first_line = it.content.splitlines()[0] if it.content else ""
            summary_lines.append(f"- Turn {idx} ({role}): {first_line[:150]}")

        if referenced_artifacts:
            summary_lines.append(
                f"Retained Data-Plane Artifact References: {', '.join(set(referenced_artifacts))}"
            )

        summary_content = "\n".join(summary_lines)
        summary_tokens = self.estimator.estimate_tokens(summary_content)

        compacted_item = ContextItem(
            layer=ContextLayerType.L5_DIALOGUE_HISTORY,
            content=summary_content,
            token_count=summary_tokens,
            priority=items[0].priority if items else 30,
            trust_level=aggregate_trust,  # Preserves untrusted taint if any turn was untrusted
            metadata={
                "is_compacted_summary": True,
                "compacted_turns_count": len(older_items),
                "referenced_artifacts": list(set(referenced_artifacts)),
            },
        )

        # Rebuild full list preserving non-L5 items and placing summary before recent turns
        new_items: list[ContextItem] = []
        dialogue_replaced = False

        for it in items:
            if it.layer != ContextLayerType.L5_DIALOGUE_HISTORY:
                new_items.append(it)
            elif not dialogue_replaced:
                new_items.append(compacted_item)
                new_items.extend(recent_items)
                dialogue_replaced = True

        return new_items, True

    def compact_observations(
        self,
        items: list[ContextItem],
        max_inline_tokens: int = 500,
    ) -> tuple[list[ContextItem], bool]:
        """Truncate or summarize older tool observations in Layer 6."""
        observation_items = [
            it for it in items if it.layer == ContextLayerType.L6_TOOL_OBSERVATIONS
        ]
        if not observation_items:
            return items, False

        was_compacted = False
        new_items: list[ContextItem] = []

        for it in items:
            if it.layer != ContextLayerType.L6_TOOL_OBSERVATIONS:
                new_items.append(it)
            else:
                # If observation is larger than max_inline_tokens, condense to excerpt
                if it.token_count > max_inline_tokens:
                    was_compacted = True
                    lines = it.content.splitlines()
                    preview = "\n".join(lines[:6])
                    condensed_content = (
                        f"{preview}\n... [Observation condensed: {len(lines)} total lines]"
                    )
                    condensed_tokens = self.estimator.estimate_tokens(condensed_content)
                    new_item = ContextItem(
                        layer=it.layer,
                        content=condensed_content,
                        token_count=condensed_tokens,
                        priority=it.priority,
                        source_id=it.source_id,
                        trust_level=it.trust_level,  # Invariant: Never elevate
                        metadata={**it.metadata, "condensed": True},
                    )
                    new_items.append(new_item)
                else:
                    new_items.append(it)

        return new_items, was_compacted
