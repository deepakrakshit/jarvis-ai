"""Structured Output Validator for External Coding Agent Harnesses.

Implements Section 144 of ARCHITECTURE.md:
"Child agents must return structured results.
 The parent JARVIS agent should not simply trust a prose statement saying 'done'."
"""

import json
import re
from typing import Any, Dict, List, Optional

import yaml  # type: ignore[import-untyped]

from jarvis.telemetry import logger

from .models import AcpValidationResult
from .sandbox import AcpSandboxManager


class AcpOutputValidator:
    """Validates structured result schemas returned by external coding agent harnesses."""

    @staticmethod
    def extract_structured_payload(raw_output: str) -> Optional[Dict[str, Any]]:
        """Extract structured YAML or JSON output from raw text or markdown code blocks."""
        if not raw_output or not raw_output.strip():
            return None

        # 1. Search for fenced code blocks with yaml or json tag
        code_block_pattern = re.compile(
            r"```(?:yaml|json)?\s*\n(.*?)\n```",
            re.DOTALL | re.IGNORECASE,
        )
        matches = code_block_pattern.findall(raw_output)

        for candidate in matches:
            candidate_clean = candidate.strip()
            # Try JSON first
            if candidate_clean.startswith("{") and candidate_clean.endswith("}"):
                try:
                    data = json.loads(candidate_clean)
                    if isinstance(data, dict):
                        res = data.get("result", data)
                        if isinstance(res, dict):
                            return res
                except Exception:
                    pass

            # Try YAML
            try:
                data = yaml.safe_load(candidate_clean)
                if isinstance(data, dict):
                    res = data.get("result", data)
                    if isinstance(res, dict):
                        return res
            except Exception:
                pass

        # 2. Try parsing the whole text directly as JSON or YAML
        trimmed = raw_output.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
                if isinstance(data, dict):
                    res = data.get("result", data)
                    if isinstance(res, dict):
                        return res
            except Exception:
                pass

        try:
            data = yaml.safe_load(trimmed)
            if isinstance(data, dict):
                res = data.get("result", data)
                if isinstance(res, dict):
                    return res
        except Exception:
            pass

        return None

    def validate(
        self,
        raw_output: str,
        sandbox: Optional[AcpSandboxManager] = None,
        verify_ground_truth_tests: bool = False,
        test_command: Optional[str] = None,
    ) -> AcpValidationResult:
        """Validate agent output against Section 144 schema and sandbox ground truth."""
        payload = self.extract_structured_payload(raw_output)

        if not payload or not isinstance(payload, dict):
            # Agent only emitted conversational prose; fail validation as per Section 144
            return AcpValidationResult(
                status="rejected",
                summary="External agent failed to provide a valid structured result schema.",
                changed_files=[],
                tests_run=[],
                tests_passed=False,
                artifacts=[],
                remaining_risks=[
                    "Unverified execution: External agent only supplied unstructured prose."
                ],
                raw_output=raw_output,
                error="Missing required Section 144 structured result payload.",
            )

        # Validate and normalize required schema fields
        raw_status = str(payload.get("status", "")).lower()
        if raw_status not in ("completed", "failed", "rejected"):
            status: Any = "failed"
        else:
            status = raw_status

        summary = str(payload.get("summary", "")).strip() or "No summary provided."

        raw_files = payload.get("changed_files", [])
        changed_files: List[str] = (
            [str(f) for f in raw_files] if isinstance(raw_files, list) else []
        )

        raw_tests = payload.get("tests_run", [])
        tests_run: List[str] = [str(t) for t in raw_tests] if isinstance(raw_tests, list) else []

        tests_passed = bool(payload.get("tests_passed", False))

        raw_artifacts = payload.get("artifacts", [])
        artifacts: List[Dict[str, Any]] = (
            [a for a in raw_artifacts if isinstance(a, dict)]
            if isinstance(raw_artifacts, list)
            else []
        )

        raw_risks = payload.get("remaining_risks", [])
        remaining_risks: List[str] = (
            [str(r) for r in raw_risks] if isinstance(raw_risks, list) else []
        )

        # Ground Truth Verification against Sandbox
        if sandbox is not None:
            # 1. Compare claimed changed files with git working tree status
            actual_git_changes = sandbox.inspect_git_changes()
            if actual_git_changes:
                claimed_set = set(changed_files)
                actual_set = set(actual_git_changes)
                unreported = actual_set - claimed_set
                if unreported:
                    remaining_risks.append(
                        f"Discrepancy: Workspace has changes not reported by agent: {sorted(unreported)}"
                    )

            # 2. Optionally run sandbox tests to verify claimed test success
            if verify_ground_truth_tests and tests_passed and (test_command or tests_run):
                cmd = test_command or (tests_run[0] if tests_run else None)
                passed, test_out = sandbox.run_sandboxed_tests(test_command=cmd)
                if not passed:
                    logger.warning("Ground-truth test verification failed!")
                    status = "failed"
                    tests_passed = False
                    remaining_risks.append(
                        f"Ground-truth test verification failed despite agent claim: {test_out[:200]}"
                    )

        return AcpValidationResult(
            status=status,
            summary=summary,
            changed_files=changed_files,
            tests_run=tests_run,
            tests_passed=tests_passed,
            artifacts=artifacts,
            remaining_risks=remaining_risks,
            raw_output=raw_output,
        )
