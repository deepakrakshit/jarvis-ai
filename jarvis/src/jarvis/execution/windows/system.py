"""System Controls (Volume, Brightness, Telemetry) for Windows Host.

Uses PyCAW for Core Audio and screen_brightness_control for displays.
"""

import platform
from typing import Any, Dict

import psutil

from jarvis.telemetry import logger


def get_system_info() -> Dict[str, Any]:
    """Retrieve host system information and hardware telemetry."""
    cpu_percent = psutil.cpu_percent(interval=0.1)
    virtual_mem = psutil.virtual_memory()
    disk_usage = psutil.disk_usage("C:\\")

    battery = psutil.sensors_battery()
    battery_info = None
    if battery:
        battery_info = {
            "percent": battery.percent,
            "power_plugged": battery.power_plugged,
            "secs_left": battery.secsleft,
        }

    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "cpu_count": psutil.cpu_count(logical=True),
        "cpu_percent": cpu_percent,
        "memory_total_gb": round(virtual_mem.total / (1024**3), 2),
        "memory_used_gb": round(virtual_mem.used / (1024**3), 2),
        "memory_percent": virtual_mem.percent,
        "disk_total_gb": round(disk_usage.total / (1024**3), 2),
        "disk_free_gb": round(disk_usage.free / (1024**3), 2),
        "disk_percent": disk_usage.percent,
        "battery": battery_info,
    }


def get_system_volume() -> Dict[str, Any]:
    """Get current master audio volume level (0 to 100)."""
    try:
        from pycaw.pycaw import AudioUtilities

        spk = AudioUtilities.GetSpeakers()
        vol = spk.EndpointVolume
        scalar = vol.GetMasterVolumeLevelScalar()
        is_muted = vol.GetMute()
        return {
            "volume_percent": round(scalar * 100),
            "is_muted": bool(is_muted),
        }
    except Exception as err:
        logger.warning(f"Could not read audio volume: {err}")
        return {"volume_percent": 100, "is_muted": False, "error": str(err)}


def set_system_volume(percent: int) -> Dict[str, Any]:
    """Set master audio volume level (0 to 100)."""
    clamped = max(0, min(100, percent))
    scalar = clamped / 100.0
    logger.info(f"Setting system volume to {clamped}% (scalar {scalar})")
    try:
        from pycaw.pycaw import AudioUtilities

        spk = AudioUtilities.GetSpeakers()
        vol = spk.EndpointVolume
        vol.SetMasterVolumeLevelScalar(scalar, None)
        return {
            "volume_percent": clamped,
            "status": "set",
        }
    except Exception as err:
        logger.error(f"Failed to set audio volume: {err}")
        raise RuntimeError(f"Failed to set audio volume: {err}") from err


def get_display_brightness() -> Dict[str, Any]:
    """Get primary monitor brightness percentage."""
    try:
        import screen_brightness_control as sbc

        brightness = sbc.get_brightness()
        current = brightness[0] if isinstance(brightness, list) else brightness
        return {"brightness_percent": current}
    except Exception as err:
        logger.warning(f"Could not read display brightness: {err}")
        return {"brightness_percent": None, "error": str(err)}


def set_display_brightness(percent: int) -> Dict[str, Any]:
    """Set primary monitor brightness percentage (0 to 100)."""
    clamped = max(0, min(100, percent))
    logger.info(f"Setting display brightness to {clamped}%")
    try:
        import screen_brightness_control as sbc

        sbc.set_brightness(clamped)
        return {
            "brightness_percent": clamped,
            "status": "set",
        }
    except Exception as err:
        logger.error(f"Failed to set display brightness: {err}")
        raise RuntimeError(f"Failed to set display brightness: {err}") from err
