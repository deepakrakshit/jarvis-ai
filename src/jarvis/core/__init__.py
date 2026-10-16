"""Core Subsystem for JARVIS Operating System."""

from typing import Any

__all__ = [
    "AppControlEngine",
    "ControlPlane",
    "ControlPlaneState",
    "app_control_engine",
    "control_plane",
]


def __getattr__(name: str) -> Any:
    """Load core services only when the requested service is used."""
    if name in {"AppControlEngine", "app_control_engine"}:
        from jarvis.core.app_control_engine import AppControlEngine, app_control_engine

        return AppControlEngine if name == "AppControlEngine" else app_control_engine

    if name in {"ControlPlane", "ControlPlaneState", "control_plane"}:
        from jarvis.core.control_plane import ControlPlane, ControlPlaneState, control_plane

        return {
            "ControlPlane": ControlPlane,
            "ControlPlaneState": ControlPlaneState,
            "control_plane": control_plane,
        }[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
