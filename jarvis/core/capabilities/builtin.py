"""JARVIS Built-in Native Capability Manifests.

Provides declarative manifests for standard core capabilities:
- File System Read/Write
- Shell Command Execution
- Web Fetch
- System Clock
"""

from jarvis.core.capabilities.manifest import (
    CapabilityManifest,
    RiskClass,
    SideEffectClass,
    ToolType,
)
from jarvis.core.capabilities.registry import CapabilityRegistry
from jarvis.core.ifc.sinks import SinkType
from jarvis.core.trust.taxonomy import TrustLevel

BUILTIN_CAPABILITIES: list[CapabilityManifest] = [
    CapabilityManifest(
        capability_id="native:fs:read_file",
        owner="core",
        version="1.0.0",
        provider="builtin",
        tool_type=ToolType.NATIVE,
        description="Reads the complete text content of a specified file within the allowed workspace.",
        required_scopes=["filesystem:read"],
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute or relative file path"}
            },
            "required": ["file_path"],
        },
        output_schema={"type": "object", "properties": {"content": {"type": "string"}}},
        risk_class=RiskClass.READ_ONLY,
        side_effect_class=SideEffectClass.NONE,
        allowed_trust_sources=[
            TrustLevel.USER_INPUT,
            TrustLevel.SYSTEM_POLICY,
            TrustLevel.EXTERNAL_UNTRUSTED,
        ],
        allowed_sinks=[SinkType.LLM_PROMPT],
        sandbox_requirement=False,
        approval_requirement=False,
    ),
    CapabilityManifest(
        capability_id="native:fs:write_file",
        owner="core",
        version="1.0.0",
        provider="builtin",
        tool_type=ToolType.NATIVE,
        description="Writes or overwrites text content to a specified file within the allowed workspace.",
        required_scopes=["filesystem:write"],
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path of the file to write"},
                "content": {"type": "string", "description": "Text content to write"},
            },
            "required": ["file_path", "content"],
        },
        output_schema={"type": "object", "properties": {"bytes_written": {"type": "integer"}}},
        risk_class=RiskClass.BOUNDED_MUTATION,
        side_effect_class=SideEffectClass.IDEMPOTENT,
        allowed_trust_sources=[TrustLevel.USER_INPUT, TrustLevel.SYSTEM_POLICY],
        allowed_sinks=[SinkType.FILE_SYSTEM_WRITE],
        sandbox_requirement=False,
        approval_requirement=True,
    ),
    CapabilityManifest(
        capability_id="native:shell:execute",
        owner="core",
        version="1.0.0",
        provider="builtin",
        tool_type=ToolType.SANDBOX,
        description="Executes a command within an isolated execution sandbox runner.",
        required_scopes=["shell:execute"],
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command line string to execute"},
                "timeout_seconds": {"type": "integer", "default": 30},
            },
            "required": ["command"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "stdout": {"type": "string"},
                "stderr": {"type": "string"},
                "exit_code": {"type": "integer"},
            },
        },
        risk_class=RiskClass.DANGEROUS,
        side_effect_class=SideEffectClass.NON_IDEMPOTENT,
        allowed_trust_sources=[TrustLevel.USER_INPUT, TrustLevel.SYSTEM_POLICY],
        allowed_sinks=[SinkType.SHELL_EXECUTION],
        sandbox_requirement=True,
        approval_requirement=True,
        verification_requirement=True,
    ),
    CapabilityManifest(
        capability_id="native:web:fetch",
        owner="core",
        version="1.0.0",
        provider="builtin",
        tool_type=ToolType.NATIVE,
        description="Fetches raw content from a permitted remote HTTP/HTTPS URL.",
        required_scopes=["network:fetch"],
        input_schema={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "HTTP or HTTPS URL to fetch"}},
            "required": ["url"],
        },
        output_schema={
            "type": "object",
            "properties": {"status_code": {"type": "integer"}, "body": {"type": "string"}},
        },
        risk_class=RiskClass.READ_ONLY,
        side_effect_class=SideEffectClass.NONE,
        allowed_trust_sources=[TrustLevel.USER_INPUT, TrustLevel.SYSTEM_POLICY],
        allowed_sinks=[SinkType.EXTERNAL_NETWORK, SinkType.LLM_PROMPT],
        sandbox_requirement=False,
        approval_requirement=False,
    ),
    CapabilityManifest(
        capability_id="native:web:search",
        owner="core",
        version="1.0.0",
        provider="builtin",
        tool_type=ToolType.NATIVE,
        description="Searches the live web and returns matching titles, URLs, and snippets.",
        required_scopes=["network:fetch"],
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query keywords"}},
            "required": ["query"],
        },
        output_schema={
            "type": "object",
            "properties": {"results": {"type": "array"}, "count": {"type": "integer"}},
        },
        risk_class=RiskClass.READ_ONLY,
        side_effect_class=SideEffectClass.NONE,
        allowed_trust_sources=[TrustLevel.USER_INPUT, TrustLevel.SYSTEM_POLICY],
        allowed_sinks=[SinkType.EXTERNAL_NETWORK, SinkType.LLM_PROMPT],
        sandbox_requirement=False,
        approval_requirement=False,
    ),
    CapabilityManifest(
        capability_id="native:clock:get_time",
        owner="core",
        version="1.0.0",
        provider="builtin",
        tool_type=ToolType.NATIVE,
        description="Returns the current UTC ISO-8601 system timestamp.",
        required_scopes=["system:clock"],
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"utc_iso": {"type": "string"}}},
        risk_class=RiskClass.READ_ONLY,
        side_effect_class=SideEffectClass.NONE,
        allowed_trust_sources=[
            TrustLevel.USER_INPUT,
            TrustLevel.SYSTEM_POLICY,
            TrustLevel.EXTERNAL_UNTRUSTED,
        ],
        allowed_sinks=[SinkType.LLM_PROMPT],
        sandbox_requirement=False,
        approval_requirement=False,
    ),
    CapabilityManifest(
        capability_id="native:calc:evaluate",
        owner="core",
        version="1.0.0",
        provider="builtin",
        tool_type=ToolType.NATIVE,
        description="Safely evaluates mathematical and arithmetic expressions using an AST evaluator.",
        required_scopes=["math:evaluate"],
        input_schema={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Mathematical expression string"}
            },
            "required": ["expression"],
        },
        output_schema={"type": "object", "properties": {"result": {"type": "number"}}},
        risk_class=RiskClass.READ_ONLY,
        side_effect_class=SideEffectClass.NONE,
        allowed_trust_sources=[
            TrustLevel.USER_INPUT,
            TrustLevel.SYSTEM_POLICY,
            TrustLevel.EXTERNAL_UNTRUSTED,
        ],
        allowed_sinks=[SinkType.LLM_PROMPT],
        sandbox_requirement=False,
        approval_requirement=False,
    ),
    CapabilityManifest(
        capability_id="native:fs:list_dir",
        owner="core",
        version="1.0.0",
        provider="builtin",
        tool_type=ToolType.NATIVE,
        description="Lists files and subdirectories within a permitted directory.",
        required_scopes=["filesystem:read"],
        input_schema={
            "type": "object",
            "properties": {"dir_path": {"type": "string", "description": "Directory path to list"}},
        },
        output_schema={"type": "object", "properties": {"entries": {"type": "array"}}},
        risk_class=RiskClass.READ_ONLY,
        side_effect_class=SideEffectClass.NONE,
        allowed_trust_sources=[
            TrustLevel.USER_INPUT,
            TrustLevel.SYSTEM_POLICY,
            TrustLevel.EXTERNAL_UNTRUSTED,
        ],
        allowed_sinks=[SinkType.LLM_PROMPT],
        sandbox_requirement=False,
        approval_requirement=False,
    ),
]


def register_builtin_capabilities(registry: CapabilityRegistry) -> None:
    """Register all standard built-in capabilities into the registry."""
    for manifest in BUILTIN_CAPABILITIES:
        manifest.seal()
        registry.register(manifest, verify_digest=True)
