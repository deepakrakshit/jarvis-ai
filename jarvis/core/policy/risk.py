"""JARVIS Dynamic Invocation Risk Calculator.

Computes dynamic risk score based on the formal architectural formula:
    InvocationRisk = f(Tool, Arguments, TargetResource, Environment, DataSensitivity)
"""

import re
from pathlib import Path
from typing import Any

from jarvis.core.capabilities.manifest import CapabilityManifest, RiskClass
from jarvis.core.ifc.labels import ConfidentialityLabel
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

# Patterns signaling high risk in parameters
DANGEROUS_SHELL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-(?:r|f|rf|fr)\b", re.IGNORECASE),
    re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE),
    re.compile(r"\b(?:mkfs|dd\s+if=)\b", re.IGNORECASE),
    re.compile(r"\bdel\s+/[fsq]\b", re.IGNORECASE),
    re.compile(r"\b(?:sudo|su|runas)\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+777\b", re.IGNORECASE),
    re.compile(r"\|\s*(?:ba|z|c)?sh\b", re.IGNORECASE),
    re.compile(r"\|\s*powershell\b", re.IGNORECASE),
]

SENSITIVE_TARGET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\.env(?:\.|$)", re.IGNORECASE),
    re.compile(r"[\\/]\.ssh[\\/]", re.IGNORECASE),
    re.compile(r"id_rsa", re.IGNORECASE),
    re.compile(r"[\\/]\.aws[\\/]", re.IGNORECASE),
    re.compile(r"[\\/]etc[\\/](?:passwd|shadow)", re.IGNORECASE),
    re.compile(r"[\\/]windows[\\/]system32", re.IGNORECASE),
    re.compile(r"credentials\.json", re.IGNORECASE),
    re.compile(r"\.git[\\/]config", re.IGNORECASE),
]

BASE_RISK_BY_CLASS: dict[RiskClass, float] = {
    RiskClass.READ_ONLY: 0.05,
    RiskClass.BOUNDED_MUTATION: 0.35,
    RiskClass.UNBOUNDED_MUTATION: 0.70,
    RiskClass.DANGEROUS: 0.95,
}

CONFIDENTIALITY_MODIFIER: dict[ConfidentialityLabel, float] = {
    ConfidentialityLabel.PUBLIC: 0.0,
    ConfidentialityLabel.INTERNAL: 0.10,
    ConfidentialityLabel.CONFIDENTIAL: 0.35,
    ConfidentialityLabel.SECRET: 0.60,
}

ENVIRONMENT_MODIFIER: dict[str, float] = {
    "development": 0.0,
    "staging": 0.10,
    "production": 0.25,
}


class RiskCalculator:
    """Evaluates dynamic execution risk for an intended tool invocation."""

    @classmethod
    def calculate_risk(
        cls,
        manifest: CapabilityManifest,
        arguments: dict[str, Any],
        target_resource: str | None = None,
        workspace_root: Path | str | None = None,
        confidentiality: ConfidentialityLabel = ConfidentialityLabel.PUBLIC,
        environment: str = "development",
    ) -> float:
        """Compute normalized dynamic invocation risk score in range [0.0, 1.0]."""
        # 1. Base score from static capability classification
        score = BASE_RISK_BY_CLASS.get(manifest.risk_class, 0.50)

        # 2. Inspect argument string contents for destructive commands
        arg_str = str(arguments)
        for pattern in DANGEROUS_SHELL_PATTERNS:
            if pattern.search(arg_str):
                score += 0.40
                break

        # 3. Inspect target resource and parameters for sensitive files / path traversal
        effective_target = target_resource or cls._extract_target_resource(arguments)
        if effective_target:
            # Check path traversal
            if ".." in effective_target or "/." in effective_target or "\\." in effective_target:
                score += 0.35

            # Check sensitive files
            for pattern in SENSITIVE_TARGET_PATTERNS:
                if pattern.search(effective_target):
                    score += 0.45
                    break

            # Check boundary confinement (is target within workspace?)
            if workspace_root and not cls._is_within_workspace(effective_target, workspace_root):
                score += 0.30

        # 4. Data sensitivity modifier
        score += CONFIDENTIALITY_MODIFIER.get(confidentiality, 0.0)

        # 5. Environment modifier
        score += ENVIRONMENT_MODIFIER.get(environment.lower(), 0.0)

        # 6. Clamp to strictly [0.0, 1.0]
        final_score = max(0.0, min(1.0, round(score, 4)))

        logger.debug(
            "dynamic_risk_calculated",
            capability_id=manifest.capability_id,
            base_risk=manifest.risk_class.value,
            confidentiality=confidentiality.value,
            target=effective_target,
            final_risk=final_score,
        )

        return final_score

    @staticmethod
    def _extract_target_resource(arguments: dict[str, Any]) -> str | None:
        """Heuristically extract file path, URL, or target entity from arguments."""
        candidate_keys = ("file_path", "path", "target", "url", "destination", "file", "uri")
        for key in candidate_keys:
            val = arguments.get(key)
            if isinstance(val, str):
                return val
        return None

    @staticmethod
    def _is_within_workspace(target: str, workspace_root: Path | str) -> bool:
        """Determine if target path resolves within allowed workspace directory."""
        try:
            target_path = Path(target).resolve()
            ws_path = Path(workspace_root).resolve()
            return ws_path in target_path.parents or target_path == ws_path
        except Exception:
            # If target cannot be resolved cleanly as a filesystem path, fail closed
            return False
