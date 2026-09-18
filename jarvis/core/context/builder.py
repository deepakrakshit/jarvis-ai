"""JARVIS Canonical 8-Layer Context Builder.

Assembles and governs context according to the zero-trust 8-layer architecture:
- L0: System / Immutable Policy
- L1: Task Objective
- L2: Current Task State
- L3: Required Tool Schemas
- L4: Relevant Governed Memories
- L5: Recent Dialogue Interaction
- L6: Governed Tool Observations
- L7: Optional Background Context

Enforces dynamic token budgeting, priority degradation, artifact offloading,
and Information Flow Control (IFC) labels.
"""

from typing import Any

from jarvis.core.context.compactor import ContextCompactor
from jarvis.core.context.estimator import TokenEstimator, create_token_estimator
from jarvis.core.context.offloader import DynamicArtifactOffloader
from jarvis.core.context.schemas import (
    ArtifactReference,
    AssembledContext,
    ContextBudget,
    ContextItem,
    ContextLayerType,
)
from jarvis.core.trust.taxonomy import TrustLevel


class ContextBuilder:
    """Orchestrates 8-layer context assembly with token budgets and artifact offloading."""

    def __init__(
        self,
        budget: ContextBudget | None = None,
        estimator: TokenEstimator | None = None,
        offloader: DynamicArtifactOffloader | None = None,
        compactor: ContextCompactor | None = None,
    ) -> None:
        self.budget = budget or ContextBudget()
        self.estimator = estimator or create_token_estimator()
        self.offloader = offloader or DynamicArtifactOffloader(
            max_inline_tokens=self.budget.max_inline_tokens,
            max_inline_bytes=self.budget.max_inline_bytes,
            estimator=self.estimator,
        )
        self.compactor = compactor or ContextCompactor(estimator=self.estimator)

    def create_item(
        self,
        layer: ContextLayerType,
        content: str,
        trust_level: TrustLevel = TrustLevel.EXTERNAL_UNTRUSTED,
        source_id: str | None = None,
        priority: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ContextItem:
        """Create a typed ContextItem with computed token count and assigned layer."""
        tokens = self.estimator.estimate_tokens(content)
        item_kwargs: dict[str, Any] = {
            "layer": layer,
            "content": content,
            "token_count": tokens,
            "trust_level": trust_level,
            "source_id": source_id,
            "metadata": metadata or {},
        }
        if priority is not None:
            item_kwargs["priority"] = priority
        return ContextItem(**item_kwargs)

    def assemble(
        self,
        task_id: str,
        items: list[ContextItem],
        system_policy: str | None = None,
        task_objective: str | None = None,
        task_state_summary: str | None = None,
        tool_schemas: str | None = None,
        memories: list[str] | None = None,
        dialogue: list[dict[str, str]] | None = None,
        observations: list[dict[str, Any]] | None = None,
        background_context: list[str] | None = None,
    ) -> AssembledContext:
        """Assemble all available layers into an AssembledContext respecting token limits."""
        assembled_items: list[ContextItem] = list(items)
        offloaded_refs: list[ArtifactReference] = []

        # 1. Inject Layer 0: System Policy (if provided)
        if system_policy:
            assembled_items.append(
                self.create_item(
                    layer=ContextLayerType.L0_SYSTEM_POLICY,
                    content=system_policy,
                    trust_level=TrustLevel.SYSTEM_POLICY,
                    source_id="system_policy",
                )
            )

        # 2. Inject Layer 1: Task Objective (if provided)
        if task_objective:
            assembled_items.append(
                self.create_item(
                    layer=ContextLayerType.L1_TASK_OBJECTIVE,
                    content=task_objective,
                    trust_level=TrustLevel.USER_INPUT,
                    source_id="user_prompt",
                )
            )

        # 3. Inject Layer 2: Task State (if provided)
        if task_state_summary:
            assembled_items.append(
                self.create_item(
                    layer=ContextLayerType.L2_TASK_STATE,
                    content=task_state_summary,
                    trust_level=TrustLevel.SYSTEM_POLICY,
                    source_id="state_machine",
                )
            )

        # 4. Inject Layer 3: Tool Schemas (if provided)
        if tool_schemas:
            assembled_items.append(
                self.create_item(
                    layer=ContextLayerType.L3_TOOL_SCHEMAS,
                    content=tool_schemas,
                    trust_level=TrustLevel.SYSTEM_POLICY,
                    source_id="capability_firewall",
                )
            )

        # 5. Inject Layer 4: Governed Memories (if provided)
        if memories:
            for idx, mem in enumerate(memories):
                assembled_items.append(
                    self.create_item(
                        layer=ContextLayerType.L4_GOVERNED_MEMORIES,
                        content=mem,
                        trust_level=TrustLevel.ARTIFACT_SEMANTICALLY_VERIFIED,
                        source_id=f"memory_{idx}",
                    )
                )

        # 6. Inject Layer 5: Dialogue History (if provided)
        if dialogue:
            for turn in dialogue:
                role = turn.get("role", "user")
                content = turn.get("content", "")
                t_level = TrustLevel.USER_INPUT if role == "user" else TrustLevel.MODEL_GENERATED
                assembled_items.append(
                    self.create_item(
                        layer=ContextLayerType.L5_DIALOGUE_HISTORY,
                        content=content,
                        trust_level=t_level,
                        metadata={"role": role},
                    )
                )

        # 7. Inject Layer 6: Tool Observations (with dynamic offloading)
        if observations:
            for obs in observations:
                tool_name = obs.get("tool_name", "unknown_tool")
                raw_payload = obs.get("payload", "")
                obs_trust = obs.get("trust_level", TrustLevel.EXTERNAL_UNTRUSTED)

                # Check if observation exceeds Control Plane budget thresholds
                if self.offloader.should_offload(raw_payload):
                    ref, rep_text = self.offloader.offload(
                        task_id=task_id,
                        content=raw_payload,
                        source_tool=tool_name,
                        trust_level=obs_trust,
                        mime_type=obs.get("mime_type", "text/plain"),
                    )
                    offloaded_refs.append(ref)
                    item_content = rep_text
                    item_metadata = {
                        "tool_name": tool_name,
                        "offloaded": True,
                        "artifact_id": ref.artifact_id,
                    }
                else:
                    item_content = (
                        str(raw_payload) if not isinstance(raw_payload, str) else raw_payload
                    )
                    item_metadata = {"tool_name": tool_name, "offloaded": False}

                assembled_items.append(
                    self.create_item(
                        layer=ContextLayerType.L6_TOOL_OBSERVATIONS,
                        content=item_content,
                        trust_level=obs_trust,
                        source_id=tool_name,
                        metadata=item_metadata,
                    )
                )

        # 8. Inject Layer 7: Background Context (if provided)
        if background_context:
            for idx, bg in enumerate(background_context):
                assembled_items.append(
                    self.create_item(
                        layer=ContextLayerType.L7_BACKGROUND_CONTEXT,
                        content=bg,
                        trust_level=TrustLevel.EXTERNAL_UNTRUSTED,
                        source_id=f"bg_{idx}",
                    )
                )

        # Check total token count
        total_tokens = sum(it.token_count for it in assembled_items)
        max_budget = self.budget.effective_input_budget
        was_compacted = False

        # If nearing or exceeding budget, perform compaction
        if total_tokens > self.budget.compaction_trigger_tokens:
            assembled_items, was_compacted = self.compactor.compact_dialogue(assembled_items)
            assembled_items, obs_compacted = self.compactor.compact_observations(
                assembled_items, max_inline_tokens=self.budget.max_inline_tokens // 2
            )
            was_compacted = was_compacted or obs_compacted
            total_tokens = sum(it.token_count for it in assembled_items)

        # If still over budget, enforce strict priority degradation
        pruned_items: list[ContextItem] = []
        if total_tokens > max_budget:
            assembled_items, pruned_items = self._prune_to_budget(assembled_items, max_budget)
            total_tokens = sum(it.token_count for it in assembled_items)

        # Format system prompt and dialogue messages
        system_prompt, messages = self._format_prompt_and_messages(assembled_items)

        return AssembledContext(
            task_id=task_id,
            items=assembled_items,
            system_prompt=system_prompt,
            messages=messages,
            total_tokens=total_tokens,
            budget=self.budget,
            pruned_items=pruned_items,
            offloaded_artifacts=offloaded_refs,
            is_compacted=was_compacted,
        )

    def _prune_to_budget(
        self,
        items: list[ContextItem],
        max_budget: int,
    ) -> tuple[list[ContextItem], list[ContextItem]]:
        """Prune items from lowest to highest priority, never pruning L0-L3 invariant layers."""
        invariant_layers = {
            ContextLayerType.L0_SYSTEM_POLICY,
            ContextLayerType.L1_TASK_OBJECTIVE,
            ContextLayerType.L2_TASK_STATE,
            ContextLayerType.L3_TOOL_SCHEMAS,
        }

        # Separate invariant items from prunable items
        invariants = [it for it in items if it.layer in invariant_layers]
        prunables = [it for it in items if it.layer not in invariant_layers]

        # Sort prunables by priority ascending (lowest priority first for removal)
        prunables.sort(key=lambda it: it.priority)

        retained_prunables: list[ContextItem] = list(prunables)
        pruned: list[ContextItem] = []

        while retained_prunables and (
            sum(it.token_count for it in invariants)
            + sum(it.token_count for it in retained_prunables)
            > max_budget
        ):
            removed = retained_prunables.pop(0)
            pruned.append(removed)

        # Maintain original order of retained items
        retained_ids = {it.item_id for it in invariants} | {it.item_id for it in retained_prunables}
        final_items = [it for it in items if it.item_id in retained_ids]

        return final_items, pruned

    def _format_prompt_and_messages(
        self, items: list[ContextItem]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert assembled items into unified system prompt and message array."""
        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []

        for it in items:
            if it.layer == ContextLayerType.L0_SYSTEM_POLICY:
                system_parts.append(f"### SYSTEM POLICY & INVARIANTS ###\n{it.content}")
            elif it.layer == ContextLayerType.L2_TASK_STATE:
                system_parts.append(f"### CURRENT TASK STATE ###\n{it.content}")
            elif it.layer == ContextLayerType.L3_TOOL_SCHEMAS:
                system_parts.append(f"### AVAILABLE CAPABILITIES ###\n{it.content}")
            elif it.layer == ContextLayerType.L4_GOVERNED_MEMORIES:
                system_parts.append(f"### GOVERNED MEMORIES ###\n{it.content}")
            elif it.layer == ContextLayerType.L7_BACKGROUND_CONTEXT:
                system_parts.append(f"### BACKGROUND CONTEXT ###\n{it.content}")
            elif it.layer == ContextLayerType.L1_TASK_OBJECTIVE:
                messages.append(
                    {
                        "role": "user",
                        "content": f"[PRIMARY TASK OBJECTIVE]\n{it.content}",
                    }
                )
            elif it.layer == ContextLayerType.L5_DIALOGUE_HISTORY:
                role = it.metadata.get("role", "user")
                messages.append({"role": role, "content": it.content})
            elif it.layer == ContextLayerType.L6_TOOL_OBSERVATIONS:
                tool_name = it.metadata.get("tool_name", "tool")
                messages.append(
                    {
                        "role": "system",
                        "content": f"[Observation: {tool_name}]\n{it.content}",
                    }
                )

        combined_system = "\n\n".join(system_parts)
        return combined_system, messages
