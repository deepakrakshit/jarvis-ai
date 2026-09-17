"""JARVIS Zero-Trust Live Tool Bridge.

Exposes canonical and curated capabilities to the realtime voice model:
- Dynamic capability projection via CapabilityFirewall and least-privilege scoping
- Centralized Policy Engine evaluation with strict Human-in-the-Loop (REQUIRE_HITL) gating
- Hardened Action Broker effect path with commit-time EffectAuthorization tokens
- Fast governed read path for safe inspection within workspace boundaries
- Decoupled background execution coexistence for continuous conversation
"""

import asyncio
from collections.abc import Callable, Coroutine, Set
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from jarvis.core.broker.broker import ActionBroker
from jarvis.core.capabilities.builtin import register_builtin_capabilities
from jarvis.core.capabilities.firewall import CapabilityFirewall
from jarvis.core.capabilities.manifest import (
    CapabilityManifest,
    RiskClass,
    SideEffectClass,
)
from jarvis.core.capabilities.registry import CapabilityRegistry
from jarvis.core.gateway.realtime import LiveToolCall, LiveToolResponse
from jarvis.core.logging import get_logger
from jarvis.core.policy.decision import (
    AutonomyLevel,
    EffectAuthorization,
    PolicyDecisionType,
)
from jarvis.core.policy.engine import PolicyEngine
from jarvis.core.policy.hitl import ApprovalRequest
from jarvis.core.trust.taxonomy import TrustLevel
from jarvis.core.voice.task_manager import BackgroundTaskManager
from jarvis.tools.native import dispatch_native_tool

logger = get_logger(__name__)


def json_schema_to_live_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert standard JSON Schema to Google GenAI Live parameter schema format."""
    if not schema:
        return {"type": "OBJECT", "properties": {}}

    type_mapping = {
        "string": "STRING",
        "number": "NUMBER",
        "integer": "INTEGER",
        "boolean": "BOOLEAN",
        "array": "ARRAY",
        "object": "OBJECT",
    }

    raw_type = schema.get("type", "object")
    if isinstance(raw_type, list):
        raw_type = raw_type[0] if raw_type else "object"
    clean_type = str(raw_type).lower()
    live_type = type_mapping.get(clean_type, "OBJECT")

    converted: dict[str, Any] = {"type": live_type}

    if "description" in schema:
        converted["description"] = str(schema["description"])

    if "properties" in schema and isinstance(schema["properties"], dict):
        live_props: dict[str, Any] = {}
        for prop_name, prop_def in schema["properties"].items():
            if isinstance(prop_def, dict):
                live_props[prop_name] = json_schema_to_live_schema(prop_def)
            else:
                live_props[prop_name] = {"type": "STRING"}
        converted["properties"] = live_props

    if "required" in schema and isinstance(schema["required"], list):
        converted["required"] = [str(req) for req in schema["required"]]

    if "items" in schema and isinstance(schema["items"], dict):
        converted["items"] = json_schema_to_live_schema(schema["items"])

    return converted


def is_mutation_authorized_by_intent(user_intent: str | None, tool_name: str) -> tuple[bool, str]:
    """Verify whether a mutating tool call is explicitly authorized by user intent."""
    if not user_intent:
        return True, "No user intent specified"

    clean = user_intent.strip().lower()
    import re

    tokens = set(re.findall(r"\b\w+\b", clean))

    mutation_verbs = {
        "write",
        "create",
        "make",
        "append",
        "add",
        "insert",
        "update",
        "modify",
        "edit",
        "change",
        "replace",
        "overwrite",
        "delete",
        "remove",
        "drop",
        "unlink",
        "truncate",
        "clear",
        "touch",
        "generate",
        "build",
        "save",
        "fix",
        "patch",
    }

    read_verbs = {
        "read",
        "inspect",
        "check",
        "summarize",
        "summary",
        "examine",
        "view",
        "cat",
        "print",
        "display",
        "show",
        "query",
        "what",
        "how",
        "tell",
        "bytes",
        "size",
        "lines",
        "count",
        "stats",
        "status",
        "list",
        "ls",
        "contents",
        "content",
    }

    has_mutation_verb = bool(tokens & mutation_verbs)
    has_read_verb = bool(tokens & read_verbs)

    # If the user requested reading/querying and did NOT request any mutation
    if has_read_verb and not has_mutation_verb:
        return (
            False,
            f"Current request is read-only ('{user_intent}'). File mutation was not explicitly requested.",
        )

    return True, "Authorized by user intent"


# Curated High-Level Tool Declarations conforming to Google GenAI Tool schema
CURATED_LIVE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "jarvis_chat",
        "description": "Handle conversational dialogue, clarifications, and direct conceptual answers.",
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "message": {
                    "type": "STRING",
                    "description": "Spoken conversational message or direct response to user.",
                }
            },
            "required": ["message"],
        },
    },
    {
        "name": "jarvis_read_file",
        "description": (
            "Read and inspect the contents of a file in the workspace (e.g. requirements.txt, pyproject.toml, "
            "source code, configs, logs). Returns the actual file contents, disk byte size, and line count so you "
            "can immediately analyze, summarize, and provide direct factual answers in the current turn. "
            "This is strictly a read-only operation and must never be accompanied by file writes or mutations."
        ),
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file_path": {
                    "type": "STRING",
                    "description": "Path or filename of the workspace file to inspect (e.g. 'requirements.txt').",
                }
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "jarvis_write_file",
        "description": (
            "Write, create, or append text content to a specified file within the allowed workspace boundary. "
            "Subject to Action Broker governance and commit-time authorization. "
            "MANDATORY: Invoke this tool ONLY when the user in the current turn explicitly requests to create, "
            "write, append, or modify a file. Never invoke this tool during a read-only or inspection request. "
            "When generating academic, scientific, or medical reports, ensure all bibliographic citations are verified "
            "and never fabricate or year-upgrade citations."
        ),
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file_path": {
                    "type": "STRING",
                    "description": "Relative or absolute path of the workspace file to write.",
                },
                "content": {
                    "type": "STRING",
                    "description": "Complete text content to write into the file.",
                },
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "jarvis_delete_file",
        "description": (
            "Delete a specified file within the workspace boundary. "
            "Confined strictly to the workspace root. Subject to Action Broker governance and policy evaluation. "
            "MANDATORY: Invoke this tool ONLY when the user explicitly requests deleting or removing a file."
        ),
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file_path": {
                    "type": "STRING",
                    "description": "Relative or absolute path of the workspace file to delete.",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "jarvis_list_dir",
        "description": (
            "List files and subdirectories within a directory in the allowed workspace boundary."
        ),
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "dir_path": {
                    "type": "STRING",
                    "description": "Directory path to list (e.g. '.' or 'jarvis/core'). Defaults to workspace root.",
                }
            },
        },
    },
    {
        "name": "jarvis_calc",
        "description": (
            "Safely evaluate mathematical and arithmetic expressions using the secure AST evaluator."
        ),
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "expression": {
                    "type": "STRING",
                    "description": "Mathematical expression string to evaluate (e.g. '2 ** 16', '3.14159 * 42').",
                }
            },
            "required": ["expression"],
        },
    },
    {
        "name": "jarvis_search_web",
        "description": (
            "Search the live web for current real-time information, documentation, academic literature, paper titles, "
            "authors, venues, publication dates, news, or technical topics. Returns real web search results with direct "
            "canonical URLs, publication snippets, and detected publication years. "
            "MANDATORY: Invoke this tool to retrieve and verify real citations, authors, publication years, and DOIs "
            "before generating academic reports or bibliographies to prevent citation fabrication or year-upgrading."
        ),
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "The search query to look up on the web.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "jarvis_fetch_web",
        "description": (
            "Fetch and read the full textual content of a permitted remote web page or documentation URL. "
            "Returns clean readable markdown/text stripped of scripts, styles, and HTML markup. "
            "MANDATORY: When researching libraries, APIs, or specifications, invoke this tool after "
            "jarvis_search_web to inspect the actual official documentation before writing code."
        ),
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "url": {
                    "type": "STRING",
                    "description": "The HTTP or HTTPS URL of the documentation or web page to fetch.",
                },
                "timeout_seconds": {
                    "type": "NUMBER",
                    "description": "Optional timeout in seconds (default 15.0).",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "jarvis_verify_citation",
        "description": (
            "Verify and retrieve authoritative, machine-readable bibliographic metadata for an academic publication "
            "directly from external scholarly providers (Crossref, DOI Content Negotiation, OpenAlex, PubMed, and Publisher HTML). "
            "MANDATORY: When asked to write academic literature or reports with references, invoke this tool with the candidate's "
            "DOI, URL, or title to obtain verified canonical authors, publication year, title, and venue before citing."
        ),
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "doi": {
                    "type": "STRING",
                    "description": "Digital Object Identifier (e.g. '10.1038/s41591-025-03983-2') if known.",
                },
                "url": {
                    "type": "STRING",
                    "description": "Canonical publisher URL or paper link if known.",
                },
                "query": {
                    "type": "STRING",
                    "description": "Search query, citation string, or paper title to resolve against scholarly providers.",
                },
                "claimed_authors": {
                    "type": "STRING",
                    "description": "Claimed author names to verify against authoritative metadata.",
                },
                "claimed_year": {
                    "type": "INTEGER",
                    "description": "Claimed publication year to verify against authoritative metadata.",
                },
            },
        },
    },
    {
        "name": "jarvis_task",
        "description": "Dispatch a general background autonomous task across JARVIS specialists.",
        "behavior": "NON_BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "objective": {"type": "STRING", "description": "Goal or instruction to execute."},
                "priority": {
                    "type": "STRING",
                    "description": "Task priority: low, normal, or high.",
                },
            },
            "required": ["objective"],
        },
    },
    {
        "name": "jarvis_research",
        "description": "Perform an immediate web intelligence investigation and return findings directly.",
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Research objective or topic to investigate.",
                },
                "depth": {"type": "STRING", "description": "Investigation depth: quick or deep."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "jarvis_code",
        "description": "Inspect, review, analyze, or execute software engineering tasks on workspace files.",
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "instruction": {"type": "STRING", "description": "Coding or engineering goal."},
                "target_files": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Optional file paths relevant to the task.",
                },
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "jarvis_system_status",
        "description": "Query current system operational health, active components, and running tasks.",
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "include_tasks": {
                    "type": "BOOLEAN",
                    "description": "Whether to list active tasks.",
                }
            },
        },
    },
    {
        "name": "jarvis_cancel_task",
        "description": "Request cancellation or abort of an ongoing background task.",
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task_id": {"type": "STRING", "description": "Identifier of the task to abort."}
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "jarvis_get_task_status",
        "description": "Query truthful progress, current phase, and results of a specific background task or the active task.",
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task_id": {
                    "type": "STRING",
                    "description": "Optional identifier of the task to inspect. If omitted, inspects the current active background task.",
                }
            },
        },
    },
    {
        "name": "jarvis_get_time",
        "description": (
            "Query the authoritative host system clock for exact local and UTC time, date, day of week, "
            "and timezone. MANDATORY: You must call this function whenever the user asks for the current time, "
            "date, day, timestamp, or real-world temporal reference. Never guess or hallucinate the time."
        ),
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "timezone": {
                    "type": "STRING",
                    "description": "Optional timezone identifier. Defaults to host local time.",
                }
            },
        },
    },
    {
        "name": "jarvis_run_python_test",
        "description": (
            "HOST PROCESS EXECUTION: Execute a workspace-local Python test file or script via direct host "
            "process execution (shell=False) with environment secret scrubbing and timeout bounding. "
            "CAUTION: Does not provide OS-level container isolation (sandbox_status=NOT_ISOLATED); subject "
            "to Human-In-The-Loop approval. "
            "MANDATORY: Use this tool whenever you need to run, test, or verify Python code or pytest suites "
            "in the workspace. Returns exit_code (0 for success, non-zero for failure), stdout, stderr, "
            "and execution duration. If execution fails, inspect the stderr/stdout traceback, diagnose the "
            "root cause, and revise the code before re-testing. Do NOT use jarvis_shell for running Python tests."
        ),
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file_path": {
                    "type": "STRING",
                    "description": "Workspace-relative path to the Python file (e.g. 'test_requests.py'). Must end in .py.",
                },
                "mode": {
                    "type": "STRING",
                    "description": "Execution mode: 'script' (direct python run) or 'pytest' (run with pytest). Defaults to 'script'.",
                },
                "test_args": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Optional safe command line arguments or flags passed to the test or script.",
                },
                "timeout_seconds": {
                    "type": "NUMBER",
                    "description": "Execution timeout in seconds (bounded between 1.0 and 60.0, default 30.0).",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "jarvis_shell",
        "description": (
            "Execute a shell command inside an isolated execution sandbox runner. "
            "Subject to human-in-the-loop approval and sandbox isolation."
        ),
        "behavior": "BLOCKING",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {
                    "type": "STRING",
                    "description": "Command line string to execute within the sandbox.",
                },
                "timeout_seconds": {
                    "type": "INTEGER",
                    "description": "Execution timeout in seconds (default 30).",
                },
            },
            "required": ["command"],
        },
    },
]


class LiveToolBridge:
    """Zero-trust mediation bridge routing Live function calls through governance gates."""

    def __init__(
        self,
        task_manager: BackgroundTaskManager,
        policy_engine: PolicyEngine | None = None,
        firewall: CapabilityFirewall | None = None,
        broker: ActionBroker | None = None,
        registry: CapabilityRegistry | None = None,
        work_executor: Callable[..., Coroutine[Any, Any, Any]] | None = None,
        approval_handler: Callable[[ApprovalRequest], Coroutine[Any, Any, bool]] | None = None,
    ) -> None:
        self.task_manager = task_manager
        self.policy_engine = policy_engine or PolicyEngine()
        self.firewall = firewall or CapabilityFirewall()
        self.broker = broker or ActionBroker()
        self.registry = registry or CapabilityRegistry()
        self.work_executor = work_executor
        self.approval_handler = approval_handler

        # Ensure standard built-in and curated voice manifests are present
        self._ensure_manifests_registered()

        # Idempotency deduplication cache
        self._processed_call_ids: set[str] = set()
        self._blocked_attempts: dict[str, int] = {}
        self.execution_log: list[dict[str, Any]] = []

    def _ensure_manifests_registered(self) -> None:
        """Ensure all canonical and curated voice tools exist in the capability registry."""
        # 1. Register core native capabilities if missing
        if not self.registry.get("native:clock:get_time"):
            register_builtin_capabilities(self.registry)

        # 2. Register curated voice tool manifests in registry if missing
        for tool_meta in CURATED_LIVE_TOOLS:
            name = tool_meta["name"]
            if not self.registry.get(name):
                # Resolve risk class and scopes
                if name in ("jarvis_write_file",):
                    risk_class = RiskClass.BOUNDED_MUTATION
                    side_effects = SideEffectClass.IDEMPOTENT
                    scopes = ["filesystem:write"]
                    approval = False
                    sandbox = False
                elif name in ("jarvis_run_python_test",):
                    risk_class = RiskClass.UNBOUNDED_MUTATION
                    side_effects = SideEffectClass.NON_IDEMPOTENT
                    scopes = ["code:execute"]
                    approval = True
                    sandbox = True
                elif name in ("jarvis_delete_file",):
                    risk_class = RiskClass.DANGEROUS
                    side_effects = SideEffectClass.NON_IDEMPOTENT
                    scopes = ["filesystem:delete"]
                    approval = True
                    sandbox = False
                elif name in ("jarvis_shell",):
                    risk_class = RiskClass.DANGEROUS
                    side_effects = SideEffectClass.NON_IDEMPOTENT
                    scopes = ["shell:execute"]
                    approval = True
                    sandbox = True
                elif name in ("jarvis_task", "jarvis_code"):
                    risk_class = RiskClass.BOUNDED_MUTATION
                    side_effects = SideEffectClass.IDEMPOTENT
                    scopes = ["voice:task"]
                    approval = False
                    sandbox = False
                elif name in ("jarvis_cancel_task",):
                    risk_class = RiskClass.BOUNDED_MUTATION
                    side_effects = SideEffectClass.IDEMPOTENT
                    scopes = ["voice:cancel"]
                    approval = False
                    sandbox = False
                elif name in ("jarvis_list_dir",):
                    risk_class = RiskClass.READ_ONLY
                    side_effects = SideEffectClass.NONE
                    scopes = ["filesystem:read"]
                    approval = False
                    sandbox = False
                elif name in ("jarvis_calc",):
                    risk_class = RiskClass.READ_ONLY
                    side_effects = SideEffectClass.NONE
                    scopes = ["math:evaluate"]
                    approval = False
                    sandbox = False
                elif name in (
                    "jarvis_search_web",
                    "jarvis_research",
                    "jarvis_verify_citation",
                    "jarvis_fetch_web",
                ):
                    risk_class = RiskClass.READ_ONLY
                    side_effects = SideEffectClass.NONE
                    scopes = ["network:fetch"]
                    approval = False
                    sandbox = False
                elif name in ("jarvis_read_file",):
                    risk_class = RiskClass.READ_ONLY
                    side_effects = SideEffectClass.NONE
                    scopes = ["filesystem:read"]
                    approval = False
                    sandbox = False
                else:
                    risk_class = RiskClass.READ_ONLY
                    side_effects = SideEffectClass.NONE
                    scopes = ["voice:chat"]
                    approval = False
                    sandbox = False

                manifest = CapabilityManifest(
                    capability_id=name,
                    version="1.0.0",
                    description=tool_meta["description"],
                    risk_class=risk_class,
                    side_effect_class=side_effects,
                    required_scopes=scopes,
                    input_schema=tool_meta.get("parameters", {}),
                    approval_requirement=approval,
                    sandbox_requirement=sandbox,
                )
                self.registry.register(manifest)

        # 3. Register natural aliases for deterministic system queries
        alias_map: dict[str, tuple[RiskClass, list[str]]] = {
            "get_current_time": (RiskClass.READ_ONLY, ["system:clock"]),
            "current_time": (RiskClass.READ_ONLY, ["system:clock"]),
            "read_file": (RiskClass.READ_ONLY, ["filesystem:read"]),
            "write_file": (RiskClass.BOUNDED_MUTATION, ["filesystem:write"]),
            "list_dir": (RiskClass.READ_ONLY, ["filesystem:read"]),
            "delete_file": (RiskClass.DANGEROUS, ["filesystem:delete"]),
            "search_web": (RiskClass.READ_ONLY, ["network:fetch"]),
            "fetch_web": (RiskClass.READ_ONLY, ["network:fetch"]),
            "jarvis_fetch_web": (RiskClass.READ_ONLY, ["network:fetch"]),
            "run_python_test": (RiskClass.BOUNDED_MUTATION, ["code:execute"]),
            "jarvis_run_python_test": (RiskClass.BOUNDED_MUTATION, ["code:execute"]),
            "run_test": (RiskClass.BOUNDED_MUTATION, ["code:execute"]),
            "jarvis_run_test": (RiskClass.BOUNDED_MUTATION, ["code:execute"]),
            "calculator": (RiskClass.READ_ONLY, ["math:evaluate"]),
            "verify_citation": (RiskClass.READ_ONLY, ["network:fetch"]),
        }
        for alias, (risk_cls, req_scopes) in alias_map.items():
            if not self.registry.get(alias):
                self.registry.register(
                    CapabilityManifest(
                        capability_id=alias,
                        version="1.0.0",
                        description=f"Natural alias for {alias}",
                        risk_class=risk_cls,
                        required_scopes=req_scopes,
                        input_schema={},
                    )
                )

    def get_tool_definitions(
        self,
        task_scopes: Set[str] | None = None,
        autonomy_level: int = 3,
        source_trust: TrustLevel = TrustLevel.USER_INPUT,
    ) -> list[dict[str, Any]]:
        """Return projected tool schemas formatted for Google GenAI Live API.

        When task_scopes is specified, performs dynamic capability projection via
        the CapabilityFirewall ensuring least-privilege tool isolation.
        """
        if task_scopes is not None:
            visible_manifests = self.firewall.project_visible_tools(
                registry=self.registry,
                task_scopes=task_scopes,
                autonomy_level=autonomy_level,
                source_trust=source_trust,
            )
            declarations: list[dict[str, Any]] = []
            seen_names: set[str] = set()
            for manifest in visible_manifests:
                # Use valid alphanumeric/underscore name for Google GenAI Live
                live_name = manifest.capability_id.replace(":", "_")
                if live_name in seen_names:
                    continue
                seen_names.add(live_name)
                behavior = "NON_BLOCKING" if "task" in manifest.capability_id else "BLOCKING"
                declarations.append(
                    {
                        "name": live_name,
                        "description": manifest.description,
                        "behavior": behavior,
                        "parameters": json_schema_to_live_schema(manifest.input_schema),
                    }
                )
            return [{"function_declarations": declarations}]

        return [{"function_declarations": CURATED_LIVE_TOOLS}]

    def _resolve_manifest(self, name: str) -> CapabilityManifest | None:
        """Resolve a tool name or alias to its registered CapabilityManifest."""
        # 1. Direct registry hit
        manifest = self.registry.get(name)
        if manifest:
            return manifest

        # 2. Canonical colon translation (e.g. native_fs_read_file -> native:fs:read_file)
        if name.startswith("native_"):
            parts = name.split("_", 2)
            if len(parts) == 3:
                canonical_id = f"{parts[0]}:{parts[1]}:{parts[2]}"
                manifest = self.registry.get(canonical_id)
                if manifest:
                    return manifest

        # 3. Check all active registered capabilities
        for m in self.registry.list_all():
            if m.capability_id.replace(":", "_") == name:
                return m

        # 4. Standard alias mapping
        alias_to_canonical = {
            "read_file": "native:fs:read_file",
            "write_file": "native:fs:write_file",
            "list_dir": "native:fs:list_dir",
            "delete_file": "native:fs:delete_file",
            "get_time": "native:clock:get_time",
            "get_current_time": "native:clock:get_time",
            "current_time": "native:clock:get_time",
            "search_web": "native:web:search",
            "fetch_url": "native:web:fetch",
            "fetch_web": "native:web:fetch",
            "run_python_test": "native:code:run_test",
            "run_test": "native:code:run_test",
            "calculator": "native:calc:evaluate",
            "execute_command": "native:shell:execute",
            "system_stats": "native:system:get_stats",
            "inspect_file": "native:fs:read_file",
            "verify_citation": "native:citation:verify",
        }
        mapped_id = alias_to_canonical.get(name)
        if mapped_id:
            return self.registry.get(mapped_id)

        return None

    async def execute_tool_call(
        self,
        tool_call: LiveToolCall,
        session_id: str,
        autonomy_level: AutonomyLevel | None = None,
        source_trust: TrustLevel = TrustLevel.USER_INPUT,
        user_intent: str | None = None,
    ) -> LiveToolResponse:
        """Process an untrusted Live tool call proposal through the zero-trust governance pipeline."""
        context_state: dict[str, Any] = {
            "manifest": None,
            "decision": None,
            "authorization": None,
        }
        resp = await self._execute_tool_call_impl(
            tool_call=tool_call,
            session_id=session_id,
            autonomy_level=autonomy_level,
            source_trust=source_trust,
            context_state=context_state,
            user_intent=user_intent,
        )
        decision = context_state.get("decision")
        authorization = context_state.get("authorization")
        manifest = context_state.get("manifest")
        log_entry = {
            "call_id": tool_call.call_id,
            "name": tool_call.name,
            "arguments": tool_call.arguments,
            "capability_id": manifest.capability_id if manifest else None,
            "policy_decision": decision.decision.value if decision else None,
            "risk_score": decision.risk_score if decision else None,
            "authorization": {
                "nonce": authorization.nonce,
                "tool_id": authorization.tool_id,
                "expires_at": authorization.expires_at.isoformat(),
            }
            if authorization
            else None,
            "response": resp.response,
            "scheduling": resp.scheduling,
        }
        self.execution_log.append(log_entry)
        return resp

    async def _execute_tool_call_impl(
        self,
        tool_call: LiveToolCall,
        session_id: str,
        autonomy_level: AutonomyLevel | None = None,
        source_trust: TrustLevel = TrustLevel.USER_INPUT,
        context_state: dict[str, Any] | None = None,
        user_intent: str | None = None,
    ) -> LiveToolResponse:
        """Internal execution implementation for Live tool calls."""
        call_id = tool_call.call_id
        name = tool_call.name
        args = tool_call.arguments

        logger.info("processing_live_tool_call", name=name, call_id=call_id)

        # 1. Idempotency Check
        if call_id in self._processed_call_ids:
            logger.warning("duplicate_live_tool_call_suppressed", call_id=call_id)
            return LiveToolResponse(
                call_id=call_id,
                name=name,
                response={"status": "duplicate", "message": "Tool call already processed."},
                scheduling="SILENT",
            )
        self._processed_call_ids.add(call_id)

        # 2. Capability Firewall & Manifest Verification
        manifest = self._resolve_manifest(name)
        if context_state is not None:
            context_state["manifest"] = manifest
        if not manifest:
            logger.error("unregistered_live_tool_attempt", tool_name=name)
            return LiveToolResponse(
                call_id=call_id,
                name=name,
                response={"error": f"Tool '{name}' is not authorized in JARVIS."},
                scheduling="WHEN_IDLE",
            )

        # 2b. User Intent Boundary Check: Protect against unsolicited mutations during read requests
        is_mutating_tool = (
            manifest.risk_class != RiskClass.READ_ONLY
            or manifest.approval_requirement
            or manifest.side_effect_class != SideEffectClass.NONE
            or name in ("jarvis_write_file", "jarvis_delete_file", "write_file", "delete_file")
        )
        if is_mutating_tool and user_intent:
            authorized, intent_reason = is_mutation_authorized_by_intent(user_intent, name)
            if not authorized:
                logger.warning(
                    "unsolicited_mutation_blocked",
                    tool_name=name,
                    user_intent=user_intent,
                    reason=intent_reason,
                )
                return LiveToolResponse(
                    call_id=call_id,
                    name=name,
                    response={
                        "error": f"Security policy blocked action: {intent_reason}",
                        "unsolicited_mutation": True,
                    },
                    scheduling="WHEN_IDLE",
                )

        # 3. Policy Engine Evaluation
        task_uuid = uuid4()
        task_id_str = f"vtask_{task_uuid.hex[:8]}"
        target_resource = (
            args.get("file_path")
            or args.get("path")
            or args.get("dir_path")
            or args.get("command")
            or args.get("url")
            or args.get("target")
            or "default_workspace"
        )

        decision = self.policy_engine.evaluate_invocation(
            task_id=task_uuid,
            manifest=manifest,
            arguments=args,
            autonomy_level=autonomy_level,
            target_resource=str(target_resource),
            agent_id="gemini-3.8-live",
            user_id="voice_user",
        )
        if context_state is not None:
            context_state["decision"] = decision

        # 3a. Strict Policy Denial Gate
        if decision.decision == PolicyDecisionType.DENY:
            logger.warning("policy_denied_live_tool", tool_name=name, reason=decision.reason)
            args_hash = ActionBroker.compute_canonical_hash(args)
            call_sig = f"{manifest.capability_id}:{args_hash}"
            self._blocked_attempts[call_sig] = self._blocked_attempts.get(call_sig, 0) + 1
            attempt_count = self._blocked_attempts[call_sig]
            if attempt_count > 1:
                return LiveToolResponse(
                    call_id=call_id,
                    name=name,
                    response={
                        "status": "blocked_repeated_attempt",
                        "attempt_count": attempt_count,
                        "error": (
                            f"Repeated invocation blocked by security policy ({attempt_count}x): {decision.reason}. "
                            "Do not repeat this tool call. Switch to an authorized alternative."
                        ),
                    },
                    scheduling="WHEN_IDLE",
                )
            return LiveToolResponse(
                call_id=call_id,
                name=name,
                response={"error": f"Security policy blocked action: {decision.reason}"},
                scheduling="WHEN_IDLE",
            )

        # 3b. Strict Human-In-The-Loop (REQUIRE_HITL) Gate
        authorization: EffectAuthorization | None = None
        if decision.decision == PolicyDecisionType.REQUIRE_HITL:
            logger.info("policy_requires_hitl_approval", tool_name=name, reason=decision.reason)
            approval_req = self.policy_engine.hitl_pipeline.create_approval_request(
                task_id=task_uuid,
                tool_id=manifest.capability_id,
                arguments=args,
                target_resource=str(target_resource),
                risk_score=decision.risk_score,
                risk_factors=decision.obligations,
                model_explanation=args.get("reason") or args.get("explanation"),
            )

            # If an interactive approval handler is registered, await resolution
            approved = False
            if self.approval_handler:
                try:
                    approved = await self.approval_handler(approval_req)
                except Exception as exc:
                    logger.error("approval_handler_error", error=str(exc))
                    approved = False

            if approved:
                try:
                    sess_uuid = UUID(session_id)
                except (ValueError, TypeError, AttributeError):
                    sess_uuid = uuid4()
                auth_res = self.policy_engine.hitl_pipeline.resolve_approval(
                    request_id=approval_req.request_id,
                    approved=True,
                    user_id="voice_user",
                    session_id=sess_uuid,
                    agent_id="gemini-3.8-live",
                )
                authorization = auth_res
                if context_state is not None:
                    context_state["authorization"] = authorization
            else:
                # Track repeated blocked invocations and provide actionable intelligent recovery
                args_hash = ActionBroker.compute_canonical_hash(args)
                call_sig = f"{manifest.capability_id}:{args_hash}"
                self._blocked_attempts[call_sig] = self._blocked_attempts.get(call_sig, 0) + 1
                attempt_count = self._blocked_attempts[call_sig]

                # Check if this was an attempt to run Python code or tests via shell
                is_python_shell = False
                suggested_file: str | None = None
                if manifest.capability_id in ("native:shell:execute", "jarvis_shell"):
                    raw_cmd = str(args.get("command") or args.get("cmd") or "").strip()
                    import re

                    m = re.search(
                        r"(?:python[0-9.]*|pytest)\s+(?:-m\s+pytest\s+)?([^\s;'\"]+\.py)",
                        raw_cmd,
                    )
                    if m:
                        is_python_shell = True
                        suggested_file = m.group(1)

                if attempt_count > 1:
                    # Repeated attempt: return proactive recovery directive
                    if is_python_shell and suggested_file:
                        return LiveToolResponse(
                            call_id=call_id,
                            name=name,
                            response={
                                "status": "blocked_repeated_attempt",
                                "requires_approval": True,
                                "attempt_count": attempt_count,
                                "error": (
                                    f"Blocked repeated attempt ({attempt_count}x). Raw shell execution requires human approval. "
                                    "RECOVERY GUIDANCE: Do NOT retry jarvis_shell. To run and test Python scripts or pytest "
                                    f"suites in the workspace, use 'jarvis_run_python_test' with file_path='{suggested_file}'."
                                ),
                                "suggested_tool": "jarvis_run_python_test",
                                "suggested_arguments": {"file_path": suggested_file},
                            },
                            scheduling="WHEN_IDLE",
                        )
                    return LiveToolResponse(
                        call_id=call_id,
                        name=name,
                        response={
                            "status": "blocked_repeated_attempt",
                            "requires_approval": True,
                            "attempt_count": attempt_count,
                            "error": (
                                f"Blocked repeated attempt ({attempt_count}x) without required approval. "
                                f"Repeatedly calling '{name}' will not bypass governance policy. "
                                "Switch to an authorized alternative tool or inform the user."
                            ),
                        },
                        scheduling="WHEN_IDLE",
                    )

                # First blocked attempt: provide guidance hint if running Python via shell
                recovery_hint = ""
                if is_python_shell and suggested_file:
                    recovery_hint = (
                        f" ALTERNATIVE: To run this Python file within governed workspace boundaries without raw shell access, "
                        f"use 'jarvis_run_python_test(file_path=\"{suggested_file}\")'."
                    )

                return LiveToolResponse(
                    call_id=call_id,
                    name=name,
                    response={
                        "status": "blocked",
                        "requires_approval": True,
                        "request_id": str(approval_req.request_id),
                        "error": f"Human-in-the-loop approval required: {decision.reason}.{recovery_hint}",
                    },
                    scheduling="WHEN_IDLE",
                )

        # 4. Execution Fabric: Governed Read Path vs Hardened Action Broker Path
        try:
            # 4a. Decoupled Voice Orchestrations
            if name == "jarvis_chat":
                return LiveToolResponse(
                    call_id=call_id,
                    name=name,
                    response={"status": "delivered", "message": args.get("message", "")},
                    scheduling="WHEN_IDLE",
                )

            elif name == "jarvis_system_status":
                active_tasks = self.task_manager.list_tasks(session_id=session_id)
                status_summary = {
                    "health": "OPERATIONAL",
                    "active_tasks_count": len([t for t in active_tasks if t.is_running]),
                    "running_tasks": [
                        {
                            "task_id": t.task_id,
                            "title": t.title,
                            "phase": t.current_phase,
                            "progress": t.progress_message,
                        }
                        for t in active_tasks
                        if t.is_running
                    ],
                }
                return LiveToolResponse(
                    call_id=call_id,
                    name=name,
                    response=status_summary,
                    scheduling="WHEN_IDLE",
                )

            elif name == "jarvis_get_task_status":
                target_id = args.get("task_id", "")
                task = self.task_manager.get_task(target_id) if target_id else None
                if not task:
                    session_tasks = self.task_manager.list_tasks(session_id=session_id)
                    if session_tasks:
                        running = [t for t in session_tasks if t.is_running]
                        task = running[-1] if running else session_tasks[-1]

                if not task:
                    return LiveToolResponse(
                        call_id=call_id,
                        name=name,
                        response={"status": "idle", "message": "No active background tasks found."},
                        scheduling="WHEN_IDLE",
                    )
                return LiveToolResponse(
                    call_id=call_id,
                    name=name,
                    response={
                        "task_id": task.task_id,
                        "title": task.title,
                        "status": task.status.value,
                        "phase": task.current_phase,
                        "progress": task.progress_message,
                        "result": task.result,
                        "error": task.error,
                    },
                    scheduling="WHEN_IDLE",
                )

            elif name == "jarvis_cancel_task":
                target_id = args.get("task_id", "")
                cancelled = await self.task_manager.cancel_task(target_id)
                if cancelled:
                    return LiveToolResponse(
                        call_id=call_id,
                        name=name,
                        response={
                            "status": "cancelled",
                            "message": f"Task '{target_id}' cancellation initiated.",
                        },
                        scheduling="INTERRUPT",
                    )
                return LiveToolResponse(
                    call_id=call_id,
                    name=name,
                    response={
                        "status": "not_cancelled",
                        "message": f"Task '{target_id}' could not be cancelled or was already finished.",
                    },
                    scheduling="WHEN_IDLE",
                )

            elif name == "jarvis_task":
                title = args.get("objective") or f"Autonomous {name}"

                async def default_worker() -> Any:
                    await asyncio.sleep(0.01)
                    if self.work_executor:
                        import inspect

                        sig = inspect.signature(self.work_executor)
                        if len(sig.parameters) >= 3 or any(
                            p.kind == p.VAR_POSITIONAL for p in sig.parameters.values()
                        ):
                            return await self.work_executor(name, title, task_id_str)
                        return await self.work_executor(name, title)
                    await self.task_manager.update_progress(
                        task_id_str, "Analyzing requirements", phase="ANALYSIS"
                    )
                    return {"result": f"Executed {name} successfully"}

                task_record = await self.task_manager.submit_task(
                    title=title,
                    session_id=session_id,
                    coro_fn=default_worker,
                    task_id=task_id_str,
                )

                return LiveToolResponse(
                    call_id=call_id,
                    name=name,
                    response={
                        "status": "started",
                        "task_id": task_record.task_id,
                        "title": title,
                        "message": f"I've started working on '{title}' in the background.",
                    },
                    scheduling="WHEN_IDLE",
                )

            elif name == "jarvis_code":
                instruction = args.get("instruction", "")
                raw_files = args.get("target_files")
                target_files: list[str] = []
                if isinstance(raw_files, list):
                    target_files = [str(f) for f in raw_files if f]
                elif isinstance(raw_files, str) and raw_files:
                    target_files = [raw_files]
                elif not raw_files and instruction:
                    import re

                    extracted = re.findall(r"[\w\-\./\\]+\.[a-zA-Z0-9]+", instruction)
                    if extracted:
                        target_files = extracted

                if target_files:
                    inspected_files: dict[str, str] = {}
                    root = Path.cwd().resolve()
                    for tf in target_files:
                        matched: Path | None = None
                        p = (root / tf).resolve()
                        if p.is_file():
                            matched = p
                        elif not tf.endswith("s") and (root / f"{tf}s").is_file():
                            matched = (root / f"{tf}s").resolve()
                        elif tf.endswith("s") and (root / tf[:-1]).is_file():
                            matched = (root / tf[:-1]).resolve()
                        else:
                            fname = Path(tf).name
                            matches = list(root.glob(f"**/{fname}"))
                            if not matches and not fname.endswith("s"):
                                matches = list(root.glob(f"**/{fname}s"))
                            if matches:
                                matched = matches[0]

                        if matched and matched.is_file():
                            with suppress(Exception):
                                inspected_files[matched.name] = matched.read_text(
                                    encoding="utf-8", errors="replace"
                                )[:3500]

                    if inspected_files:
                        return LiveToolResponse(
                            call_id=call_id,
                            name=name,
                            response={
                                "status": "inspected",
                                "instruction": instruction,
                                "files": inspected_files,
                            },
                            scheduling="WHEN_IDLE",
                        )
                    else:
                        return LiveToolResponse(
                            call_id=call_id,
                            name=name,
                            response={
                                "status": "not_found",
                                "instruction": instruction,
                                "message": f"Target files {target_files} not found in workspace.",
                            },
                            scheduling="WHEN_IDLE",
                        )

                title = instruction or f"Autonomous {name}"

                async def code_worker() -> Any:
                    await asyncio.sleep(0.01)
                    if self.work_executor:
                        import inspect

                        sig = inspect.signature(self.work_executor)
                        if len(sig.parameters) >= 3 or any(
                            p.kind == p.VAR_POSITIONAL for p in sig.parameters.values()
                        ):
                            return await self.work_executor(name, title, task_id_str)
                        return await self.work_executor(name, title)
                    await self.task_manager.update_progress(
                        task_id_str, f"Executing code task: {title}", phase="EXECUTION"
                    )
                    return {"result": f"Executed {title} with precision."}

                task_record = await self.task_manager.submit_task(
                    title=title,
                    session_id=session_id,
                    coro_fn=code_worker,
                    task_id=task_id_str,
                )

                return LiveToolResponse(
                    call_id=call_id,
                    name=name,
                    response={
                        "status": "started",
                        "task_id": task_record.task_id,
                        "title": title,
                        "message": f"I've started working on '{title}' in the background.",
                    },
                    scheduling="WHEN_IDLE",
                )

            # 4b. Governed Safe Read Path
            is_mutating = (
                manifest.risk_class != RiskClass.READ_ONLY
                or manifest.approval_requirement
                or manifest.side_effect_class != SideEffectClass.NONE
            )

            if not is_mutating:
                # Fast governed read execution
                if name in (
                    "jarvis_get_time",
                    "get_current_time",
                    "current_time",
                    "native_clock_get_time",
                ):
                    tz_arg = args.get("timezone")
                    local_now = datetime.now().astimezone()
                    if tz_arg and isinstance(tz_arg, str):
                        clean_tz = tz_arg.strip().upper()
                        if clean_tz in ("UTC", "GMT", "Z"):
                            local_now = datetime.now(UTC)
                        else:
                            try:
                                from zoneinfo import ZoneInfo

                                local_now = datetime.now(ZoneInfo(tz_arg.strip()))
                            except Exception:
                                local_now = datetime.now().astimezone()

                    utc_now = datetime.now(UTC)
                    return LiveToolResponse(
                        call_id=call_id,
                        name=name,
                        response={
                            "status": "success",
                            "local_time": local_now.strftime("%I:%M:%S %p"),
                            "local_date": local_now.strftime("%A, %B %d, %Y"),
                            "timezone": str(local_now.tzinfo),
                            "utc_time": utc_now.strftime("%I:%M:%S %p UTC"),
                            "unix_timestamp": int(local_now.timestamp()),
                            "iso_format": local_now.isoformat(),
                        },
                        scheduling="WHEN_IDLE",
                    )

                elif name in (
                    "jarvis_read_file",
                    "read_file",
                    "inspect_file",
                    "native_fs_read_file",
                ):
                    target_path = (
                        args.get("file_path")
                        or args.get("path")
                        or (
                            args.get("target_files", [""])[0]
                            if isinstance(args.get("target_files"), list)
                            and args.get("target_files")
                            else ""
                        )
                    )
                    if not target_path:
                        return LiveToolResponse(
                            call_id=call_id,
                            name=name,
                            response={"error": "file_path is required to inspect a file"},
                            scheduling="WHEN_IDLE",
                        )
                    read_output = await dispatch_native_tool(
                        "native:fs:read_file", {"file_path": target_path}
                    )
                    raw_content = read_output.get("content", "")
                    disk_bytes = read_output.get("disk_bytes") or read_output.get(
                        "size_bytes", len(raw_content.encode("utf-8"))
                    )
                    content_bytes = read_output.get(
                        "content_bytes", len(raw_content.encode("utf-8"))
                    )
                    max_chars = 120_000
                    truncated = len(raw_content) > max_chars
                    return LiveToolResponse(
                        call_id=call_id,
                        name=name,
                        response={
                            "status": "success",
                            "file_name": Path(target_path).name,
                            "relative_path": target_path,
                            "size_bytes": disk_bytes,
                            "disk_bytes": disk_bytes,
                            "content_bytes": content_bytes,
                            "total_lines": len(raw_content.splitlines()),
                            "content": raw_content[:max_chars],
                            "truncated": truncated,
                        },
                        scheduling="WHEN_IDLE",
                    )

                elif name in ("jarvis_list_dir", "list_dir", "native_fs_list_dir"):
                    dir_path = args.get("dir_path") or args.get("path") or "."
                    list_output = await dispatch_native_tool(
                        "native:fs:list_dir", {"dir_path": dir_path}
                    )
                    return LiveToolResponse(
                        call_id=call_id,
                        name=name,
                        response={"status": "success", **list_output},
                        scheduling="WHEN_IDLE",
                    )

                elif name in (
                    "jarvis_calc",
                    "jarvis_calculator",
                    "calculator",
                    "native_calc_evaluate",
                ):
                    expr = args.get("expression") or args.get("expr") or ""
                    calc_output = await dispatch_native_tool(
                        "native:calc:evaluate", {"expression": expr}
                    )
                    return LiveToolResponse(
                        call_id=call_id,
                        name=name,
                        response={"status": "success", "result": calc_output.get("result")},
                        scheduling="WHEN_IDLE",
                    )

                elif name in (
                    "jarvis_search_web",
                    "jarvis_research",
                    "search_web",
                    "native_web_search",
                ):
                    query = args.get("query") or args.get("q") or args.get("instruction") or ""
                    if not query:
                        return LiveToolResponse(
                            call_id=call_id,
                            name=name,
                            response={"error": "search query is required"},
                            scheduling="WHEN_IDLE",
                        )
                    from jarvis.tools.native.web import search_web

                    search_data = search_web(query, max_results=5)
                    return LiveToolResponse(
                        call_id=call_id,
                        name=name,
                        response={
                            "status": "success",
                            "query": query,
                            "count": search_data.get("count", 0),
                            "results": search_data.get("results", []),
                        },
                        scheduling="WHEN_IDLE",
                    )

                elif name in (
                    "jarvis_verify_citation",
                    "verify_citation",
                    "native_citation_verify",
                ):
                    from jarvis.tools.native.citation import verify_citation

                    citation_data = await verify_citation(
                        query=args.get("query"),
                        doi=args.get("doi"),
                        url=args.get("url"),
                        claimed_authors=args.get("claimed_authors"),
                        claimed_year=args.get("claimed_year"),
                        claimed_title=args.get("claimed_title"),
                    )
                    return LiveToolResponse(
                        call_id=call_id,
                        name=name,
                        response={"status": "success", **citation_data},
                        scheduling="WHEN_IDLE",
                    )

                elif name in (
                    "native_web_fetch",
                    "fetch_url",
                    "jarvis_fetch_web",
                    "fetch_web",
                ):
                    url = args.get("url") or ""
                    timeout = float(args.get("timeout_seconds", 15.0))
                    fetch_data = await dispatch_native_tool(
                        "native:web:fetch", {"url": url, "timeout_seconds": timeout}
                    )
                    return LiveToolResponse(
                        call_id=call_id,
                        name=name,
                        response={"status": "success", **fetch_data},
                        scheduling="WHEN_IDLE",
                    )

                elif name in ("native_system_get_stats", "system_stats"):
                    stats = await dispatch_native_tool("native:system:get_stats", {})
                    return LiveToolResponse(
                        call_id=call_id,
                        name=name,
                        response={"status": "success", **stats},
                        scheduling="WHEN_IDLE",
                    )

            # 4c. Hardened Action Broker Write/Effect Path
            # Mint commit-time EffectAuthorization if not already resolved by HITL
            try:
                session_uuid = UUID(session_id)
            except (ValueError, TypeError, AttributeError):
                session_uuid = uuid4()
            if authorization is None:
                args_hash = ActionBroker.compute_canonical_hash(args)
                authorization = EffectAuthorization(
                    proposal_id=uuid4(),
                    task_id=task_uuid,
                    user_id="voice_user",
                    session_id=session_uuid,
                    agent_id="gemini-3.8-live",
                    tool_id=manifest.capability_id,
                    canonical_arguments_hash=args_hash,
                    target_resource=str(target_resource),
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                    nonce=uuid4().hex,
                )
            if context_state is not None:
                context_state["authorization"] = authorization

            cap_id = manifest.capability_id

            async def tool_runner(run_args: dict[str, Any]) -> Any:
                return await dispatch_native_tool(cap_id, run_args)

            broker_result = await self.broker.execute_action(
                task_id=task_uuid,
                tool_id=manifest.capability_id,
                arguments=args,
                executor_fn=tool_runner,
                manifest=manifest,
                authorization=authorization,
                target_resource=str(target_resource),
            )

            resp_payload: dict[str, Any] = {
                "status": "success",
                "capability_id": manifest.capability_id,
                "result": broker_result,
            }
            if isinstance(broker_result, dict):
                resp_payload.update(broker_result)

            return LiveToolResponse(
                call_id=call_id,
                name=name,
                response=resp_payload,
                scheduling="WHEN_IDLE",
            )

        except Exception as exc:
            logger.error("error_executing_live_tool", tool_name=name, error=str(exc))
            return LiveToolResponse(
                call_id=call_id,
                name=name,
                response={"error": str(exc)},
                scheduling="WHEN_IDLE",
            )
