"""JARVIS Operational Metrics and Resource Accounting Subsystem.

Implements ARCHITECTURE.md Layer 19: Latency distributions, token accounting,
quota headroom tracking, verification success rates, and DLQ depth indicators.
"""

import math
from datetime import UTC, datetime
from threading import RLock
from typing import Any


class OperationalMetrics:
    """Thread-safe aggregator tracking operational performance and resource consumption."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.created_at = datetime.now(UTC)

        # Latency tracking: category -> list of floats
        self._latencies: dict[str, list[float]] = {}

        # Model usage tracking: provider:model -> counts
        self._model_usage: dict[str, dict[str, Any]] = {}

        # Tool execution tracking: tool_id -> counts
        self._tool_metrics: dict[str, dict[str, Any]] = {}

        # Verification metrics
        self._verification_attempts: int = 0
        self._verification_passes: int = 0
        self._verification_failures: int = 0

        # DLQ metrics
        self._dlq_quarantine_count: int = 0
        self._dlq_replay_count: int = 0

        # Quota headroom metrics: provider -> request count
        self._provider_requests: dict[str, int] = {}

    def record_latency(self, category: str, duration_ms: float) -> None:
        """Record an execution duration measurement."""
        with self._lock:
            if category not in self._latencies:
                self._latencies[category] = []
            self._latencies[category].append(max(0.0, float(duration_ms)))
            # Keep bounded memory
            if len(self._latencies[category]) > 5000:
                self._latencies[category] = self._latencies[category][-5000:]

    def record_model_call(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: float,
        cached_tokens: int = 0,
        estimated_cost_usd: float | None = None,
    ) -> None:
        """Record model token consumption, latency, and estimated cost."""
        key = f"{provider}:{model}"
        with self._lock:
            self.record_latency(f"model:{key}", duration_ms)

            if key not in self._model_usage:
                self._model_usage[key] = {
                    "total_calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cached_tokens": 0,
                    "total_tokens": 0,
                    "estimated_cost_usd": 0.0,
                }

            rec = self._model_usage[key]
            rec["total_calls"] += 1
            rec["prompt_tokens"] += max(0, prompt_tokens)
            rec["completion_tokens"] += max(0, completion_tokens)
            rec["cached_tokens"] += max(0, cached_tokens)
            rec["total_tokens"] += max(0, prompt_tokens + completion_tokens)
            if estimated_cost_usd is not None:
                rec["estimated_cost_usd"] += max(0.0, estimated_cost_usd)

            self._provider_requests[provider] = self._provider_requests.get(provider, 0) + 1

    def record_tool_execution(
        self,
        tool_id: str,
        duration_ms: float,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Record execution outcome and latency for a capability or tool."""
        with self._lock:
            self.record_latency(f"tool:{tool_id}", duration_ms)

            if tool_id not in self._tool_metrics:
                self._tool_metrics[tool_id] = {
                    "total_calls": 0,
                    "successes": 0,
                    "failures": 0,
                    "last_error": None,
                }

            rec = self._tool_metrics[tool_id]
            rec["total_calls"] += 1
            if success:
                rec["successes"] += 1
            else:
                rec["failures"] += 1
                rec["last_error"] = error

    def record_verification(self, verified: bool, duration_ms: float) -> None:
        """Record an external-state verification outcome."""
        with self._lock:
            self.record_latency("verification:all", duration_ms)
            self._verification_attempts += 1
            if verified:
                self._verification_passes += 1
            else:
                self._verification_failures += 1

    def record_dlq_event(self, action: str) -> None:
        """Record DLQ activity (quarantine, replay, purge)."""
        with self._lock:
            if action == "quarantine":
                self._dlq_quarantine_count += 1
            elif action == "replay":
                self._dlq_replay_count += 1

    @staticmethod
    def _compute_percentiles(values: list[float]) -> dict[str, float]:
        if not values:
            return {"count": 0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0}
        s = sorted(values)
        n = len(s)

        def _pct(p: float) -> float:
            idx = math.ceil((p / 100.0) * n) - 1
            return round(s[max(0, min(idx, n - 1))], 2)

        return {
            "count": n,
            "p50": _pct(50),
            "p90": _pct(90),
            "p95": _pct(95),
            "p99": _pct(99),
            "mean": round(sum(s) / n, 2),
        }

    def get_summary(self) -> dict[str, Any]:
        """Compile a complete snapshot of operational and accounting metrics."""
        with self._lock:
            latencies_summary = {
                cat: self._compute_percentiles(vals) for cat, vals in self._latencies.items()
            }

            total_prompt = sum(m["prompt_tokens"] for m in self._model_usage.values())
            total_comp = sum(m["completion_tokens"] for m in self._model_usage.values())
            total_cost = sum(m["estimated_cost_usd"] for m in self._model_usage.values())

            verify_rate = (
                round((self._verification_passes / self._verification_attempts) * 100.0, 1)
                if self._verification_attempts > 0
                else 100.0
            )

            return {
                "timestamp": datetime.now(UTC).isoformat(),
                "uptime_seconds": round((datetime.now(UTC) - self.created_at).total_seconds(), 1),
                "token_accounting": {
                    "total_prompt_tokens": total_prompt,
                    "total_completion_tokens": total_comp,
                    "total_tokens": total_prompt + total_comp,
                    "total_cost_usd": round(total_cost, 4),
                    "per_model": dict(self._model_usage),
                },
                "provider_requests": dict(self._provider_requests),
                "tool_metrics": dict(self._tool_metrics),
                "verification": {
                    "total_attempts": self._verification_attempts,
                    "passes": self._verification_passes,
                    "failures": self._verification_failures,
                    "success_rate_percent": verify_rate,
                },
                "dead_letter_queue": {
                    "quarantined_total": self._dlq_quarantine_count,
                    "replayed_total": self._dlq_replay_count,
                },
                "latencies_ms": latencies_summary,
            }

    def reset(self) -> None:
        """Reset all metrics (useful for isolated tests)."""
        with self._lock:
            self._latencies.clear()
            self._model_usage.clear()
            self._tool_metrics.clear()
            self._verification_attempts = 0
            self._verification_passes = 0
            self._verification_failures = 0
            self._dlq_quarantine_count = 0
            self._dlq_replay_count = 0
            self._provider_requests.clear()
            self.created_at = datetime.now(UTC)


_DEFAULT_METRICS: OperationalMetrics | None = None


def get_metrics() -> OperationalMetrics:
    """Retrieve or initialize the default OperationalMetrics singleton."""
    global _DEFAULT_METRICS
    if _DEFAULT_METRICS is None:
        _DEFAULT_METRICS = OperationalMetrics()
    return _DEFAULT_METRICS
