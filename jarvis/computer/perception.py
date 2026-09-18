"""Computer Perception subsystem for capturing, validating, and formatting desktop frames."""

from __future__ import annotations

import base64
import hashlib
import io
import time
from uuid import uuid4

from PIL import Image

from jarvis.computer.models import ScreenshotMetadata, ScreenshotResult
from jarvis.computer.windows_api import WindowsAPI
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

try:
    import mss
except Exception:
    mss = None  # type: ignore[assignment]


class ScreenPerception:
    """High-assurance desktop screenshot capture and validation engine."""

    def __init__(self, windows_api: WindowsAPI | None = None) -> None:
        self.windows_api = windows_api or WindowsAPI()

    def capture(
        self,
        output_format: str = "bytes",
        max_dimension: int = 1280,
        quality: int = 80,
        monitor_index: int = 1,
        include_cursor: bool = False,
        test_double_image: Image.Image | None = None,
    ) -> ScreenshotResult:
        """Capture the live desktop surface, downscale proportionally, and validate integrity.

        Args:
            output_format: 'bytes', 'base64', or 'both'.
            max_dimension: Max bounding dimension for downscaling (default 1280).
            quality: JPEG quality (1-100).
            monitor_index: 1-indexed display monitor.
            include_cursor: Whether to overlay mouse cursor position.
            test_double_image: Optional PIL Image for headless unit tests.

        Returns:
            Validated ScreenshotResult with metadata and image bytes.
        """
        self.windows_api.init_dpi_awareness()
        self.windows_api.attach_to_default_desktop()

        img: Image.Image | None = None

        if test_double_image is not None:
            img = test_double_image
        else:
            # Check desktop state
            state = self.windows_api.detect_desktop_state()
            if state in ("LOCKED_DESKTOP", "UNAVAILABLE"):
                return ScreenshotResult(
                    status="unavailable",
                    metadata=ScreenshotMetadata(
                        width=0,
                        height=0,
                        original_width=0,
                        original_height=0,
                        monitor_index=monitor_index,
                    ),
                    error_code="DISPLAY_SURFACE_UNAVAILABLE",
                    message="Desktop surface is unavailable (workstation is locked or headless).",
                )

            # Strategy 1: MSS capture (fastest multi-monitor capture)
            if mss is not None:
                try:
                    mss_factory = getattr(mss, "MSS", getattr(mss, "mss", None))
                    if mss_factory is not None:
                        with mss_factory() as sct:
                            monitors = sct.monitors
                            if monitors:
                                target = (
                                    monitors[monitor_index]
                                    if monitor_index < len(monitors)
                                    else monitors[0]
                                )
                                sct_img = sct.grab(target)
                                img = Image.frombytes(
                                    "RGB", sct_img.size, sct_img.bgra, "raw", "BGRX"
                                )
                except Exception as exc:
                    logger.debug("mss_capture_failed", error=str(exc))
                    img = None

            # Strategy 2: Fallback to PIL ImageGrab
            if img is None:
                try:
                    from PIL import ImageGrab

                    img = ImageGrab.grab()
                except Exception as exc:
                    logger.debug("imagegrab_failed", error=str(exc))
                    img = None

        if img is None:
            return ScreenshotResult(
                status="error",
                metadata=ScreenshotMetadata(
                    width=0,
                    height=0,
                    original_width=0,
                    original_height=0,
                    monitor_index=monitor_index,
                ),
                error_code="SCREEN_CAPTURE_FAILED",
                message="Failed to capture desktop display frame from host graphics pipeline.",
            )

        orig_w, orig_h = img.size

        # Downscale proportionally if needed
        if max(orig_w, orig_h) > max_dimension:
            scale = max_dimension / max(orig_w, orig_h)
            new_w = max(1, int(orig_w * scale))
            new_h = max(1, int(orig_h * scale))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        else:
            new_w, new_h = orig_w, orig_h

        # Encode to JPEG
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        raw_bytes = buf.getvalue()

        # Validate image integrity
        validation_err = self.validate_image_bytes(raw_bytes)
        if validation_err:
            return ScreenshotResult(
                status="error",
                metadata=ScreenshotMetadata(
                    width=new_w,
                    height=new_h,
                    original_width=orig_w,
                    original_height=orig_h,
                    monitor_index=monitor_index,
                ),
                error_code="IMAGE_VALIDATION_FAILED",
                message=f"Corrupted image payload: {validation_err}",
            )

        sha256 = hashlib.sha256(raw_bytes).hexdigest()

        meta = ScreenshotMetadata(
            capture_id=str(uuid4()),
            timestamp=time.time(),
            width=new_w,
            height=new_h,
            original_width=orig_w,
            original_height=orig_h,
            monitor_index=monitor_index,
            mime_type="image/jpeg",
            byte_size=len(raw_bytes),
            sha256_digest=sha256,
        )

        b64_data: str | None = None
        if output_format in ("base64", "both"):
            b64_data = base64.b64encode(raw_bytes).decode("utf-8")

        return ScreenshotResult(
            status="success",
            metadata=meta,
            raw_bytes=raw_bytes if output_format in ("bytes", "both") else b"",
            base64_data=b64_data,
            message=f"Desktop display frame captured ({new_w}x{new_h}, {len(raw_bytes):,} bytes).",
        )

    @staticmethod
    def validate_image_bytes(data: bytes) -> str | None:
        """Validate that bytes constitute a structurally sound JPEG image."""
        if not data:
            return "Empty image buffer (0 bytes)."
        if len(data) < 100:
            return f"Image buffer is suspiciously small ({len(data)} bytes)."

        # JPEG SOI (\xff\xd8) or PNG (\x89PNG) header verification
        if not data.startswith(b"\xff\xd8") and not data.startswith(b"\x89PNG"):
            return "Missing standard JPEG (0xFFD8) or PNG header magic bytes."

        # Verify pillow can open and verify the stream
        try:
            test_buf = io.BytesIO(data)
            with Image.open(test_buf) as im:
                im.verify()
        except Exception as exc:
            return f"PIL verification failed: {exc}"

        return None
