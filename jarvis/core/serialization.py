"""Canonical JSON-safe serialization engine for JARVIS.

Ensures all domain entities, UUIDs, datetimes, Enums, Paths, Pydantic models,
and complex data structures cross JSON and WebSocket boundaries deterministically.
"""

from __future__ import annotations

import enum
import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, TypeAdapter

# Reusable TypeAdapter for general Python object to JSON-safe representation
_OBJECT_ADAPTER = TypeAdapter(object)


def to_json_safe(obj: Any) -> Any:
    """Recursively convert any Python object into a canonical JSON-serializable primitive.

    Handles:
    - UUID -> string
    - datetime, date, time -> ISO 8601 formatted string
    - Enum -> value or string name
    - Path -> POSIX / system string path
    - Pydantic BaseModel -> model_dump(mode="json")
    - Exception -> string representation
    - dict -> recursive sanitization of all keys and values
    - list, tuple, set -> recursive sanitization of all items
    - Fallback: TypeAdapter(object).dump_python(mode="json") or str(obj)
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj

    if isinstance(obj, UUID):
        return str(obj)

    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()

    if isinstance(obj, enum.Enum):
        return obj.value if isinstance(obj.value, (str, int, float, bool)) else str(obj.value)

    if isinstance(obj, Path):
        return str(obj)

    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")

    if isinstance(obj, Exception):
        return str(obj)

    if isinstance(obj, dict):
        return {str(k): to_json_safe(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set)):
        return [to_json_safe(item) for item in obj]

    # Use Pydantic's mode='json' conversion as high-speed standard
    try:
        return _OBJECT_ADAPTER.dump_python(obj, mode="json")
    except Exception:
        return str(obj)


def canonical_json_dumps(obj: Any, **kwargs: Any) -> str:
    """Serialize any Python object to a JSON string deterministically.

    All UUIDs, datetimes, Enums, and custom types are guaranteed to be serialized
    safely without throwing TypeError.
    """
    safe_obj = to_json_safe(obj)
    if "default" not in kwargs:
        kwargs["default"] = str
    return json.dumps(safe_obj, **kwargs)


def canonical_json_loads(s: str | bytes) -> Any:
    """Deserialize a JSON string or bytes safely."""
    return json.loads(s)
