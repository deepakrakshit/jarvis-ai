"""Tests for WSL2, user Linux distributions, and Docker Desktop detection semantics.

Verifies the separation of:
1. WSL platform engine availability (WSL_PLATFORM_AVAILABLE)
2. User Linux distribution presence (USER_WSL_DISTROS_PRESENT)
3. Docker Desktop installation state (DOCKER_DESKTOP_INSTALLED, including all-users and per-user)
4. Docker CLI availability (DOCKER_CLI_AVAILABLE)
5. Docker daemon availability (DOCKER_DAEMON_AVAILABLE)
6. Docker backend resolution (DOCKER_BACKEND, fail-closed on UNKNOWN)
7. Docker server version (DOCKER_SERVER_VERSION)
8. Multi-source evidence evaluation and contradiction detection
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from jarvis.sandbox.detector import (
    BackendIdentityResolver,
    CapabilityDetector,
    evaluate_profile_satisfaction,
)
from jarvis.sandbox.models import (
    AttestationStatus,
    BackendType,
    CapabilityStatus,
    DockerBackendType,
    EvidenceClassification,
    EvidenceProvenance,
    IsolationTier,
    ProviderCategory,
    SandboxAttestation,
    SandboxProfile,
    SentinelSuiteReport,
    SystemCapabilities,
)


def test_all_users_docker_desktop_installation_detected() -> None:
    """Test 1: All-users Docker Desktop installation in %PROGRAMFILES% is detected."""
    detector = CapabilityDetector()
    diagnostics: list[str] = []

    def fake_exists(path_obj: Path) -> bool:
        normalized = str(path_obj).replace("/", "\\")
        return normalized == r"C:\Program Files\Docker\Docker\Docker Desktop.exe"

    with (
        patch.dict("os.environ", {"PROGRAMFILES": r"C:\Program Files"}, clear=True),
        patch.object(Path, "exists", fake_exists),
        patch("shutil.which", return_value=None),
    ):
        status = detector._probe_docker_desktop_installed("windows", diagnostics)
        assert status == CapabilityStatus.SUPPORTED
        assert len(diagnostics) == 0


def test_per_user_docker_desktop_installation_detected() -> None:
    """Test 2: Per-user Docker Desktop installation in %LOCALAPPDATA%\\Programs\\DockerDesktop is detected."""
    detector = CapabilityDetector()
    diagnostics: list[str] = []

    def fake_exists(path_obj: Path) -> bool:
        normalized = str(path_obj).replace("/", "\\")
        return (
            normalized
            == r"C:\Users\testuser\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe"
        )

    with (
        patch.dict(
            "os.environ",
            {
                "PROGRAMFILES": r"C:\Program Files",
                "LOCALAPPDATA": r"C:\Users\testuser\AppData\Local",
            },
            clear=True,
        ),
        patch.object(Path, "exists", fake_exists),
        patch("shutil.which", return_value=None),
    ):
        status = detector._probe_docker_desktop_installed("windows", diagnostics)
        assert status == CapabilityStatus.SUPPORTED
        assert len(diagnostics) == 0


def test_docker_desktop_installed_but_daemon_unavailable() -> None:
    """Test 3: Docker Desktop installed but daemon unavailable -> daemon unavailable."""
    detector = CapabilityDetector()

    with (
        patch("platform.system", return_value="Windows"),
        patch("platform.machine", return_value="AMD64"),
        patch(
            "shutil.which",
            side_effect=lambda cmd: (
                r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
                if cmd == "docker"
                else None
            ),
        ),
        patch.object(
            detector, "_probe_docker_desktop_installed", return_value=CapabilityStatus.SUPPORTED
        ),
        patch.object(detector, "_probe_docker_daemon", return_value=None),
    ):
        caps = detector.detect()

        # Installation and CLI are present
        assert caps.docker_desktop_installed == CapabilityStatus.SUPPORTED
        assert caps.docker_cli_available == CapabilityStatus.SUPPORTED

        # Daemon is unavailable, so backend is unavailable and Tier 1 is excluded
        assert caps.docker_daemon_available == CapabilityStatus.UNAVAILABLE
        assert caps.docker_backend == DockerBackendType.UNAVAILABLE
        assert IsolationTier.TIER_1_CONTAINER not in caps.supported_tiers


def test_cli_unavailable_while_docker_desktop_installed() -> None:
    """Test 4: CLI unavailable while Docker Desktop installation is present."""
    detector = CapabilityDetector()

    with (
        patch("platform.system", return_value="Windows"),
        patch("platform.machine", return_value="AMD64"),
        patch("shutil.which", return_value=None),  # docker.exe is NOT in PATH
        patch.object(
            detector, "_probe_docker_desktop_installed", return_value=CapabilityStatus.SUPPORTED
        ),
    ):
        caps = detector.detect()

        # Desktop is installed, but CLI is unavailable
        assert caps.docker_desktop_installed == CapabilityStatus.SUPPORTED
        assert caps.docker_cli_available == CapabilityStatus.UNAVAILABLE
        assert caps.docker_daemon_available == CapabilityStatus.UNAVAILABLE
        assert caps.docker_backend == DockerBackendType.UNAVAILABLE
        assert IsolationTier.TIER_1_CONTAINER not in caps.supported_tiers


def test_wsl_available_zero_user_distros_daemon_unavailable() -> None:
    """Scenario 1: WSL available with zero user distros and Docker Desktop daemon unavailable."""
    detector = CapabilityDetector()

    wsl_ev = EvidenceProvenance(
        source="wsl_runtime",
        observed_value="no installed distributions",
        normalized_value="ZERO_DISTROS",
        confidence="HIGH",
    )

    with (
        patch("platform.system", return_value="Windows"),
        patch("platform.machine", return_value="AMD64"),
        patch(
            "shutil.which",
            side_effect=lambda cmd: (
                r"C:\Windows\System32\wsl.exe" if cmd in ("wsl.exe", "wsl") else None
            ),
        ),
        patch.object(detector, "_probe_wsl_runtime_evidence", return_value=([], [wsl_ev])),
        patch.object(
            detector, "_probe_docker_desktop_installed", return_value=CapabilityStatus.SUPPORTED
        ),
        patch.object(detector, "_probe_docker_daemon", return_value=None),
    ):
        caps = detector.detect()

        # WSL platform is available, 0 user distros
        assert caps.wsl_platform_available == CapabilityStatus.SUPPORTED
        assert caps.user_wsl_distros_present == CapabilityStatus.UNAVAILABLE
        assert len(caps.wsl_distributions) == 0

        # Docker Desktop is installed, but daemon is offline
        assert caps.docker_desktop_installed == CapabilityStatus.SUPPORTED
        assert caps.docker_daemon_available == CapabilityStatus.UNAVAILABLE
        assert caps.docker_backend == DockerBackendType.UNAVAILABLE
        assert IsolationTier.TIER_1_CONTAINER not in caps.supported_tiers


def test_wsl_available_zero_user_distros_daemon_available_affirmative_wsl2() -> None:
    """Scenario 2: WSL available with zero user distros and Docker daemon available with affirmative WSL2."""
    detector = CapabilityDetector()

    mock_docker_info: dict[str, Any] = {
        "ServerVersion": "27.2.0",
        "OperatingSystem": "Docker Desktop",
        "KernelVersion": "5.15.153.1-microsoft-standard-WSL2",
        "OSType": "linux",
        "DefaultRuntime": "runc",
        "SecurityOptions": ["name=seccomp,profile=default", "name=userns"],
        "CgroupVersion": "2",
        "CgroupDriver": "cgroupfs",
        "MemoryLimit": True,
        "CpuCfsQuota": True,
        "PidsLimit": True,
        "Warnings": [],
    }

    wsl_ev = EvidenceProvenance(
        source="wsl_runtime",
        observed_value={"distributions": ["docker-desktop"], "docker_running": True},
        normalized_value="WSL2_RUNNING",
        confidence="HIGH",
    )

    with (
        patch("platform.system", return_value="Windows"),
        patch("platform.machine", return_value="AMD64"),
        patch(
            "shutil.which",
            side_effect=lambda cmd: (
                r"C:\Windows\System32\wsl.exe"
                if cmd in ("wsl.exe", "wsl")
                else (
                    r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
                    if cmd == "docker"
                    else None
                )
            ),
        ),
        patch.object(
            detector,
            "_probe_wsl_runtime_evidence",
            return_value=(["docker-desktop"], [wsl_ev]),
        ),
        patch.object(
            detector, "_probe_docker_desktop_installed", return_value=CapabilityStatus.SUPPORTED
        ),
        patch.object(detector, "_probe_docker_daemon", return_value=mock_docker_info),
    ):
        caps = detector.detect()

        # WSL platform is present, 0 user distros
        assert caps.wsl_platform_available == CapabilityStatus.SUPPORTED
        assert caps.user_wsl_distros_present == CapabilityStatus.UNAVAILABLE

        # Docker Desktop and daemon operational
        assert caps.docker_desktop_installed == CapabilityStatus.SUPPORTED
        assert caps.docker_cli_available == CapabilityStatus.SUPPORTED
        assert caps.docker_daemon_available == CapabilityStatus.SUPPORTED
        assert caps.docker_server_version == "27.2.0"

        # Resolves WSL2 backend without requiring user distributions
        assert caps.docker_backend == DockerBackendType.WSL2
        assert caps.backend_confidence == "HIGH"
        assert IsolationTier.TIER_1_CONTAINER in caps.supported_tiers


def test_wsl_platform_available_user_distro_present_docker_unavailable() -> None:
    """Scenario 3: WSL platform available + user distros present + Docker Desktop unavailable."""
    detector = CapabilityDetector()

    wsl_ev = EvidenceProvenance(
        source="wsl_runtime",
        observed_value={"distributions": ["Ubuntu-22.04", "Debian"], "docker_running": False},
        normalized_value="NO_DOCKER_WSL_DISTRO",
        confidence="HIGH",
    )

    with (
        patch("platform.system", return_value="Windows"),
        patch("platform.machine", return_value="AMD64"),
        patch(
            "shutil.which",
            side_effect=lambda cmd: (
                r"C:\Windows\System32\wsl.exe" if cmd in ("wsl.exe", "wsl") else None
            ),
        ),
        patch.object(
            detector,
            "_probe_wsl_runtime_evidence",
            return_value=(["Ubuntu-22.04", "Debian"], [wsl_ev]),
        ),
        patch.object(
            detector, "_probe_docker_desktop_installed", return_value=CapabilityStatus.UNAVAILABLE
        ),
        patch.object(detector, "_probe_docker_daemon", return_value=None),
    ):
        caps = detector.detect()

        # WSL platform and user distros are available
        assert caps.wsl_platform_available == CapabilityStatus.SUPPORTED
        assert caps.user_wsl_distros_present == CapabilityStatus.SUPPORTED
        assert "Ubuntu-22.04" in caps.wsl_distributions
        assert "Debian" in caps.wsl_distributions

        # Independent state: Docker is unavailable
        assert caps.docker_desktop_installed == CapabilityStatus.UNAVAILABLE
        assert caps.docker_daemon_available == CapabilityStatus.UNAVAILABLE
        assert caps.docker_backend == DockerBackendType.UNAVAILABLE
        assert IsolationTier.TIER_1_CONTAINER not in caps.supported_tiers


def test_docker_desktop_distro_stopped_without_affirmative_runtime_resolves_unknown() -> None:
    """Scenario 4: docker-desktop distro present (stopped) without affirmative WSL2 runtime -> UNKNOWN."""
    evidence = [
        EvidenceProvenance(
            source="wsl_runtime",
            observed_value={"distributions": ["docker-desktop"], "docker_running": False},
            normalized_value="WSL2_PRESENT",
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_kernel",
            observed_value="6.1.0-generic",
            normalized_value="6.1.0-generic",
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_os",
            observed_value="Docker Desktop",
            normalized_value="Docker Desktop",
            confidence="HIGH",
        ),
    ]

    result = BackendIdentityResolver.resolve(
        evidence_list=evidence, host_os="windows", daemon_available=True
    )

    # Must NOT auto-classify as WSL2; fails closed to UNKNOWN
    assert result.active_backend == DockerBackendType.UNKNOWN
    assert result.confidence == "NONE"


def test_docker_daemon_available_ambiguous_backend_evidence_resolves_unknown() -> None:
    """Scenario 5: Docker daemon available with ambiguous backend evidence resolves to UNKNOWN."""
    evidence = [
        EvidenceProvenance(
            source="docker_info_kernel",
            observed_value="6.1.0-virtual",
            normalized_value="6.1.0-virtual",
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_os",
            observed_value="Linux Container Host",
            normalized_value="Linux Container Host",
            confidence="HIGH",
        ),
    ]

    result = BackendIdentityResolver.resolve(
        evidence_list=evidence, host_os="windows", daemon_available=True
    )

    assert result.active_backend == DockerBackendType.UNKNOWN
    assert result.confidence == "NONE"
    assert len(result.contradictions) == 0


def test_docker_daemon_available_contradictory_backend_evidence_resolves_unknown() -> None:
    """Scenario 6: Docker daemon available with contradictory backend evidence resolves to UNKNOWN."""
    evidence = [
        # Configuration says WSL2
        EvidenceProvenance(
            source="docker_settings",
            observed_value={"wslEngineEnabled": True},
            normalized_value="WSL2",
            classification=EvidenceClassification.CONFIGURATION_EVIDENCE,
            confidence="HIGH",
        ),
        # Runtime says non-WSL generic kernel
        EvidenceProvenance(
            source="docker_info_kernel",
            observed_value="5.15.0-generic",
            normalized_value="5.15.0-generic",
            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
            confidence="HIGH",
        ),
    ]

    result = BackendIdentityResolver.resolve(
        evidence_list=evidence, host_os="windows", daemon_available=True
    )

    # Fails closed to UNKNOWN with recorded contradiction
    assert result.active_backend == DockerBackendType.UNKNOWN
    assert result.configured_backend == DockerBackendType.WSL2
    assert result.confidence == "LOW"
    assert len(result.contradictions) > 0
    assert any("not a WSL2 kernel" in c for c in result.contradictions)


def test_configured_non_wsl_runtime_wsl2_contradiction_resolves_unknown() -> None:
    """Scenario 6b: Configured non-WSL (wslEngineEnabled: false) but runtime kernel is WSL2 -> UNKNOWN with contradiction."""
    evidence = [
        EvidenceProvenance(
            source="docker_settings",
            observed_value={"wslEngineEnabled": False},
            normalized_value="NON_WSL",
            classification=EvidenceClassification.CONFIGURATION_EVIDENCE,
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_kernel",
            observed_value="5.15.153.1-microsoft-standard-WSL2",
            normalized_value="5.15.153.1-microsoft-standard-WSL2",
            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
            confidence="HIGH",
        ),
    ]

    result = BackendIdentityResolver.resolve(
        evidence_list=evidence, host_os="windows", daemon_available=True
    )

    assert result.active_backend == DockerBackendType.UNKNOWN
    assert result.confidence == "LOW"
    assert len(result.contradictions) > 0
    assert any("contradicts active WSL2 kernel" in c for c in result.contradictions)


def test_docker_daemon_available_only_wsl_platform_exists_not_automatically_wsl2() -> None:
    """Scenario 7: Docker daemon available + only WSL exists evidence -> NOT automatically WSL2."""
    evidence = [
        EvidenceProvenance(
            source="wsl_platform",
            observed_value=r"C:\Windows\System32\wsl.exe",
            normalized_value="SUPPORTED",
            classification=EvidenceClassification.CORROBORATING_EVIDENCE,
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_kernel",
            observed_value="5.10.0-linux-generic",
            normalized_value="5.10.0-linux-generic",
            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_os",
            observed_value="Docker Desktop",
            normalized_value="Docker Desktop",
            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
            confidence="HIGH",
        ),
    ]

    result = BackendIdentityResolver.resolve(
        evidence_list=evidence, host_os="windows", daemon_available=True
    )

    # Presence of wsl.exe alone does NOT prove active engine is WSL2
    assert result.active_backend == DockerBackendType.UNKNOWN
    assert result.confidence == "NONE"


def test_docker_daemon_available_only_wsl_disabled_not_automatically_hyperv_or_vmm() -> None:
    """Scenario 8: Docker daemon available + only WSL disabled evidence -> NOT automatically Hyper-V/VMM."""
    evidence = [
        EvidenceProvenance(
            source="docker_settings",
            observed_value={"wslEngineEnabled": False},
            normalized_value="NON_WSL",
            classification=EvidenceClassification.CONFIGURATION_EVIDENCE,
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_kernel",
            observed_value="5.10.0-linux-custom",
            normalized_value="5.10.0-linux-custom",
            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_os",
            observed_value="Docker Desktop",
            normalized_value="Docker Desktop",
            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
            confidence="HIGH",
        ),
    ]

    result = BackendIdentityResolver.resolve(
        evidence_list=evidence, host_os="windows", daemon_available=True
    )

    # wslEngineEnabled: false alone does not prove Hyper-V or Docker VMM; must resolve UNKNOWN
    assert result.active_backend == DockerBackendType.UNKNOWN
    assert result.configured_backend == DockerBackendType.UNKNOWN
    assert result.confidence == "NONE"


def test_docker_info_isolation_hyperv_resolves_unknown() -> None:
    """Scenario 9: docker info Isolation='hyperv' is container isolation mode, NOT backend identity.
    Must resolve to UNKNOWN, never inferring HYPER_V.
    """
    evidence = [
        EvidenceProvenance(
            source="docker_info_isolation",
            observed_value="hyperv",
            normalized_value="hyperv",
            classification=EvidenceClassification.CONTAINER_ISOLATION_EVIDENCE,
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_kernel",
            observed_value="5.15.0-generic",
            normalized_value="5.15.0-generic",
            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_os",
            observed_value="Docker Desktop",
            normalized_value="Docker Desktop",
            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
            confidence="HIGH",
        ),
    ]

    result = BackendIdentityResolver.resolve(
        evidence_list=evidence, host_os="windows", daemon_available=True
    )

    assert result.active_backend == DockerBackendType.UNKNOWN
    assert result.confidence == "NONE"
    assert len(result.contradictions) == 0


def test_docker_desktop_engine_ls_does_not_infer_backend() -> None:
    """Scenario 10: docker desktop engine ls describes container mode (e.g. linux vs windows), not hypervisor backend.
    Must not cause resolver to guess backend choice.
    """
    evidence = [
        EvidenceProvenance(
            source="docker_desktop_cli_engine",
            observed_value="*linux\nwindows",
            normalized_value="linux",
            classification=EvidenceClassification.CONTAINER_ISOLATION_EVIDENCE,
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_kernel",
            observed_value="5.15.0-generic",
            normalized_value="5.15.0-generic",
            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
            confidence="HIGH",
        ),
    ]

    result = BackendIdentityResolver.resolve(
        evidence_list=evidence, host_os="windows", daemon_available=True
    )

    assert result.active_backend == DockerBackendType.UNKNOWN
    assert result.confidence == "NONE"


def test_docker_daemon_corroborated_wsl2_resolution() -> None:
    """Scenario 10b: Corroborated WSL2 configuration and runtime evidence resolves to WSL2."""
    evidence = [
        EvidenceProvenance(
            source="docker_settings",
            observed_value={"wslEngineEnabled": True},
            normalized_value="WSL2",
            classification=EvidenceClassification.CONFIGURATION_EVIDENCE,
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_kernel",
            observed_value="5.15.153.1-microsoft-standard-WSL2",
            normalized_value="5.15.153.1-microsoft-standard-WSL2",
            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="wsl_runtime",
            observed_value={"distributions": ["docker-desktop"], "docker_running": True},
            normalized_value="WSL2_RUNNING",
            classification=EvidenceClassification.CORROBORATING_EVIDENCE,
            confidence="HIGH",
        ),
    ]

    result = BackendIdentityResolver.resolve(
        evidence_list=evidence, host_os="windows", daemon_available=True
    )

    assert result.active_backend == DockerBackendType.WSL2
    assert result.configured_backend == DockerBackendType.WSL2
    assert result.confidence == "HIGH"
    assert len(result.contradictions) == 0


def test_configured_wsl2_runtime_incompatible_resolves_unknown() -> None:
    """Scenario 11: Configured WSL2 but runtime evidence incompatible -> UNKNOWN with contradiction."""
    evidence = [
        EvidenceProvenance(
            source="docker_settings",
            observed_value={"wslEngineEnabled": True},
            normalized_value="WSL2",
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_kernel",
            observed_value="6.1.0-generic-linux",
            normalized_value="6.1.0-generic-linux",
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_os",
            observed_value="Docker Desktop",
            normalized_value="Docker Desktop",
            confidence="HIGH",
        ),
    ]

    result = BackendIdentityResolver.resolve(
        evidence_list=evidence, host_os="windows", daemon_available=True
    )

    assert result.active_backend == DockerBackendType.UNKNOWN
    assert result.configured_backend == DockerBackendType.WSL2
    assert result.confidence == "LOW"
    assert len(result.contradictions) > 0
    assert any("not a WSL2 kernel" in c for c in result.contradictions)


def test_configured_non_wsl_runtime_inconclusive_resolves_unknown() -> None:
    """Scenario 12: Configured non-WSL backend + runtime evidence inconclusive -> UNKNOWN."""
    evidence = [
        EvidenceProvenance(
            source="docker_settings",
            observed_value={"wslEngineEnabled": False},
            normalized_value="NON_WSL",
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_kernel",
            observed_value="5.15.0-generic",
            normalized_value="5.15.0-generic",
            confidence="HIGH",
        ),
    ]

    result = BackendIdentityResolver.resolve(
        evidence_list=evidence, host_os="windows", daemon_available=True
    )

    assert result.active_backend == DockerBackendType.UNKNOWN
    assert result.confidence == "NONE"


def test_unknown_backend_fails_closed_for_verified_profile() -> None:
    """Scenario 13: Unknown backend fails closed for a profile requiring verified containment."""
    caps = SystemCapabilities(
        host_os="windows",
        host_arch="amd64",
        docker_cli_available=CapabilityStatus.SUPPORTED,
        docker_daemon_available=CapabilityStatus.SUPPORTED,
        docker_backend=DockerBackendType.UNKNOWN,
        supported_tiers=(IsolationTier.TIER_0_LOCAL, IsolationTier.TIER_1_CONTAINER),
    )

    profile = SandboxProfile(
        profile_id="strict-workload", isolation_tier=IsolationTier.TIER_1_CONTAINER
    )
    satisfaction = evaluate_profile_satisfaction(profile, caps)

    # Fail closed invariant: an unverified backend type cannot be accepted
    assert satisfaction.is_satisfied is False
    assert any("UNKNOWN" in reason for reason in satisfaction.unsatisfied_reasons)


def test_known_backend_without_profile_attestation_is_unverified() -> None:
    """Scenario 14: Known backend without profile attestation remains unverified."""
    attestation = SandboxAttestation(
        sandbox_id=uuid4(),
        profile_id="standard-container",
        backend_type=BackendType.DOCKER,
        docker_backend=DockerBackendType.WSL2,
        backend_version="27.2.0",
        isolation_tier=IsolationTier.TIER_1_CONTAINER,
        provider_category=ProviderCategory.REAL_ISOLATION_PROVIDER,
        status=AttestationStatus.NOT_VERIFIED,
    )

    # Even with a known WSL2 backend, status is NOT_VERIFIED until empirical sentinels run
    assert attestation.docker_backend == DockerBackendType.WSL2
    assert attestation.status == AttestationStatus.NOT_VERIFIED
    assert attestation.filesystem_isolation == CapabilityStatus.UNKNOWN


def test_known_backend_without_real_isolation_verification() -> None:
    """Scenario 15: Known backend without real isolation verification reports real_isolation_verified = False."""
    report = SentinelSuiteReport(
        sandbox_id=uuid4(),
        backend_type=BackendType.DOCKER,
        docker_backend=DockerBackendType.WSL2,
        provider_category=ProviderCategory.REAL_ISOLATION_PROVIDER,
        isolation_tier=IsolationTier.TIER_1_CONTAINER,
        all_probes_passed=False,
        real_isolation_verified=False,
        diagnostics=("Synthetic test double verification only; operational isolation not proven.",),
    )

    assert report.docker_backend == DockerBackendType.WSL2
    assert report.all_probes_passed is False
    assert report.real_isolation_verified is False


def test_native_linux_backend_resolution() -> None:
    """Test 16: Native Linux host with standard kernel resolves to NATIVE_LINUX."""
    evidence = [
        EvidenceProvenance(
            source="docker_info_kernel",
            observed_value="6.5.0-35-generic",
            normalized_value="6.5.0-35-generic",
            confidence="HIGH",
        ),
        EvidenceProvenance(
            source="docker_info_os",
            observed_value="Ubuntu 22.04.4 LTS",
            normalized_value="Ubuntu 22.04.4 LTS",
            confidence="HIGH",
        ),
    ]

    result = BackendIdentityResolver.resolve(
        evidence_list=evidence, host_os="linux", daemon_available=True
    )

    assert result.active_backend == DockerBackendType.NATIVE_LINUX
    assert result.configured_backend == DockerBackendType.NATIVE_LINUX
    assert result.confidence == "HIGH"
    assert len(result.contradictions) == 0


def test_isolation_hyperv_with_wsl2_kernel_preserves_wsl2_active_backend() -> None:
    """Test 17: Container isolation 'hyperv' does not invalidate or contradict affirmative WSL2 Linux kernel."""
    evidence = [
        # WSL2 kernel indicator
        EvidenceProvenance(
            source="docker_info_kernel",
            observed_value="5.15.153.1-microsoft-standard-WSL2",
            normalized_value="5.15.153.1-microsoft-standard-WSL2",
            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
            confidence="HIGH",
        ),
        # Windows container isolation mode present on daemon
        EvidenceProvenance(
            source="docker_info_isolation",
            observed_value="hyperv",
            normalized_value="hyperv",
            classification=EvidenceClassification.CONTAINER_ISOLATION_EVIDENCE,
            confidence="HIGH",
        ),
    ]

    result = BackendIdentityResolver.resolve(
        evidence_list=evidence, host_os="windows", daemon_available=True
    )

    # Affirmative WSL2 runtime is correctly identified; Isolation='hyperv' is container isolation, not VM conflict
    assert result.active_backend == DockerBackendType.WSL2
    assert result.confidence in ("HIGH", "MEDIUM")
    assert len(result.contradictions) == 0
