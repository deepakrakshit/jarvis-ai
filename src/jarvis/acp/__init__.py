"""Agent Control Protocol (ACP) and External Coding Agent Subsystem.

Implements Sections 30, 143, and 144 of ARCHITECTURE.md:
- External agent harness session brokering
- Workspace sandboxing and path confinement
- Policy-gated tool execution and permission relay
- Section 144 structured result verification
"""

from .broker import JarvisAgentBroker
from .models import (
    AcpEvent,
    AcpPermissionOption,
    AcpPermissionRequest,
    AcpPermissionResponse,
    AcpRole,
    AcpSessionSpec,
    AcpSessionState,
    AcpValidationResult,
)
from .permission_relay import AcpPermissionRelay
from .sandbox import AcpSandboxError, AcpSandboxManager
from .validator import AcpOutputValidator

__all__ = [
    "JarvisAgentBroker",
    "AcpSessionSpec",
    "AcpSessionState",
    "AcpRole",
    "AcpValidationResult",
    "AcpPermissionOption",
    "AcpPermissionRequest",
    "AcpPermissionResponse",
    "AcpEvent",
    "AcpSandboxManager",
    "AcpSandboxError",
    "AcpPermissionRelay",
    "AcpOutputValidator",
]
