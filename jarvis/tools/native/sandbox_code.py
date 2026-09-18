"""JARVIS Canonical Sandbox Code Execution Tool.

Executes code or tests inside an isolated container sandbox backend (TIER_1_CONTAINER).
Enforces the absolute fail-closed invariant: if container isolation is unavailable,
execution fails closed immediately with SandboxBackendUnavailableError.
Host fallback is strictly prohibited.
"""

from __future__ import annotations

import shlex
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from jarvis.core.exceptions import (
    SandboxBackendUnavailableError,
    SandboxExecutionError,
    SandboxTimeoutError,
)
from jarvis.core.logging import get_logger
from jarvis.sandbox.detector import evaluate_profile_satisfaction, get_system_capabilities
from jarvis.sandbox.models import (
    CapabilityStatus,
    IsolationTier,
    NetworkProfile,
    ProviderCategory,
    ResourceLimits,
    SandboxProfile,
    SandboxState,
)
from jarvis.sandbox.security import (
    normalize_sandbox_relative_path,
    validate_host_path_for_staging,
    validate_sandbox_profile_security,
)

if TYPE_CHECKING:
    from jarvis.sandbox.manager import SandboxLifecycleManager

logger = get_logger(__name__)


async def execute_in_sandbox(
    command: list[str] | str,
    timeout_seconds: float = 30.0,
    files: dict[str, str] | None = None,
    profile_id: str = "sandbox_python_exec",
    task_id: UUID | None = None,
    manager: SandboxLifecycleManager | None = None,
    allow_test_doubles: bool = False,
) -> dict[str, Any]:
    """Execute command within an isolated execution sandbox.

    Args:
        command: Command string or list of argument tokens to execute.
        timeout_seconds: Execution timeout in seconds (bounded 1.0 to 120.0).
        files: Optional mapping of relative sandbox paths to string contents to upload.
        profile_id: Sandbox profile identifier.
        task_id: Bound JARVIS task ID.
        manager: Optional SandboxLifecycleManager instance.
        allow_test_doubles: Permit test double providers (strictly for contract/unit tests).

    Returns:
        Structured dictionary conforming to sandbox:code:execute output schema.

    Raises:
        SandboxBackendUnavailableError: If container backend is offline/unavailable.
            NO host fallback is ever performed.
    """
    bound_task_id = task_id or uuid4()
    bounded_timeout = min(max(float(timeout_seconds), 1.0), 120.0)

    # 1. Parse command into token list
    cmd_list: list[str]
    if isinstance(command, str):
        cmd_str = command.strip()
        if not cmd_str:
            raise SandboxExecutionError("Command string cannot be empty.")
        cmd_list = shlex.split(cmd_str)
    elif isinstance(command, list):
        cmd_list = [str(c) for c in command if str(c).strip()]
        if not cmd_list:
            raise SandboxExecutionError("Command argument list cannot be empty.")
    else:
        raise SandboxExecutionError("Command must be a string or list of argument strings.")

    # 2. Check Host Virtualization / Container Engine Reality
    caps = get_system_capabilities()
    daemon_available = caps.docker_daemon_available == CapabilityStatus.SUPPORTED

    if not daemon_available and not allow_test_doubles:
        reasons = "; ".join(caps.diagnostics) if caps.diagnostics else "Docker daemon is inactive"
        logger.error(
            "sandbox_execution_failed_closed",
            reason=reasons,
            task_id=str(bound_task_id),
        )
        raise SandboxBackendUnavailableError(
            f"Fail-closed invariant triggered: Real container sandbox backend is unavailable on this host ({reasons}). "
            "Automatic unisolated host execution fallback is strictly prohibited."
        )

    # 3. Construct Declarative Profile
    profile = SandboxProfile(
        profile_id=profile_id,
        isolation_tier=IsolationTier.TIER_1_CONTAINER,
        base_image="python:3.11-slim",
        run_as_user="1000:1000",
        read_only_rootfs=True,
        no_new_privileges=True,
        drop_capabilities=("ALL",),
        network_profile=NetworkProfile.NONE,
        resource_limits=ResourceLimits(
            timeout_seconds=bounded_timeout,
            cpu_limit=1.0,
            memory_limit_mb=512,
            pids_limit=100,
        ),
    )

    # Validate security constraints
    sec_violations = validate_sandbox_profile_security(profile)
    if sec_violations:
        raise SandboxExecutionError(f"Security validation failed: {'; '.join(sec_violations)}")

    # 4. Check profile satisfaction against backend capabilities
    satisfaction = evaluate_profile_satisfaction(profile, caps)
    if not satisfaction.is_satisfied and not allow_test_doubles:
        raise SandboxBackendUnavailableError(
            f"Requested sandbox profile cannot be satisfied by host backend: {'; '.join(satisfaction.unsatisfied_reasons)}"
        )

    # 5. Resolve Lifecycle Manager
    mgr = manager
    if mgr is None:
        from jarvis.sandbox.manager import SandboxLifecycleManager
        from jarvis.sandbox.registry import get_sandbox_registry

        mgr = SandboxLifecycleManager(registry=get_sandbox_registry())

    # 6. Instantiate Sandbox
    identity = await mgr.create_sandbox(
        task_id=bound_task_id,
        profile=profile,
        allow_test_doubles=allow_test_doubles,
    )
    sandbox_id = identity.sandbox_id

    # 7. Execute Lifecycle: Start -> Upload -> Execute -> Attest -> Terminate
    try:
        await mgr.start_sandbox(sandbox_id)

        # Upload Input Files with Path Confinement and Integrity Validation
        if files:
            with tempfile.TemporaryDirectory(prefix="jarvis_upload_prep_") as tmp_dir:
                tmp_root = Path(tmp_dir)
                artifacts_to_upload: list[tuple[Path, str]] = []
                for rel_path, content in files.items():
                    safe_rel = normalize_sandbox_relative_path(rel_path)
                    local_file = tmp_root / safe_rel.replace("/", "_")
                    local_file.write_text(content, encoding="utf-8")
                    safe_local = validate_host_path_for_staging(local_file, allowed_root=tmp_root)
                    artifacts_to_upload.append((safe_local, safe_rel))

                await mgr.upload_artifacts(
                    sandbox_id=sandbox_id,
                    task_id=bound_task_id,
                    artifacts=artifacts_to_upload,
                )

        exec_result = await mgr.execute_command(
            sandbox_id=sandbox_id,
            command=cmd_list,
            task_id=bound_task_id,
            capability_id="sandbox:code:execute",
            timeout_seconds=bounded_timeout,
        )

        # Attestation check
        attestation = await mgr.attest_sandbox(sandbox_id)
        is_real = identity.provider_category == ProviderCategory.REAL_ISOLATION_PROVIDER
        sandbox_status = (
            "SANDBOX_ACTIVE"
            if (is_real and attestation.status.value == "VERIFIED")
            else ("SANDBOX_ACTIVE_UNATTESTED" if is_real else "TEST_DOUBLE")
        )

        return {
            "exit_code": exec_result.exit_code,
            "stdout": exec_result.stdout,
            "stderr": exec_result.stderr,
            "duration_seconds": exec_result.duration_seconds,
            "timed_out": exec_result.timed_out,
            "sandbox_id": str(sandbox_id),
            "isolation_tier": identity.isolation_tier.value,
            "provider_category": identity.provider_category.value,
            "sandbox_status": sandbox_status,
        }

    except SandboxTimeoutError:
        logger.warning("sandbox_code_execution_timed_out", sandbox_id=str(sandbox_id))
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Sandbox execution timed out after {bounded_timeout} seconds.",
            "duration_seconds": bounded_timeout,
            "timed_out": True,
            "sandbox_id": str(sandbox_id),
            "isolation_tier": identity.isolation_tier.value,
            "provider_category": identity.provider_category.value,
            "sandbox_status": "TIMED_OUT",
        }
    finally:
        # Enforce lifecycle termination and absence verification
        try:
            term_state = await mgr.terminate_sandbox(sandbox_id, reason="execution_completed")
            if term_state != SandboxState.TERMINATED:
                logger.warning(
                    "sandbox_termination_unverified",
                    sandbox_id=str(sandbox_id),
                    state=term_state.value,
                )
        except Exception as term_exc:
            logger.error(
                "sandbox_termination_error",
                sandbox_id=str(sandbox_id),
                error=str(term_exc),
            )


__all__ = ["execute_in_sandbox"]
