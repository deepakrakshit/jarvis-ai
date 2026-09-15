"""JARVIS Native Clock Tool.

Provides point-in-time timestamps and calendar information.
"""

from datetime import UTC, datetime
from typing import Any


def get_time() -> dict[str, Any]:
    """Return current UTC and local timestamps."""
    now_utc = datetime.now(UTC)
    now_local = datetime.now()
    return {
        "utc_iso": now_utc.isoformat(),
        "local_iso": now_local.isoformat(),
        "unix_timestamp": now_utc.timestamp(),
        "day_of_week": now_utc.strftime("%A"),
    }
