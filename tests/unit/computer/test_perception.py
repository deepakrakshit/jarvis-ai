"""Unit tests for ScreenPerception subsystem."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from jarvis.computer.perception import ScreenPerception


@pytest.fixture
def perception() -> ScreenPerception:
    return ScreenPerception()


@pytest.fixture
def test_image_1080p() -> Image.Image:
    return Image.new("RGB", (1920, 1080), color=(30, 60, 90))


def test_perception_capture_test_double(
    perception: ScreenPerception, test_image_1080p: Image.Image
) -> None:
    """Verify screen capture with synthetic test double image."""
    result = perception.capture(
        output_format="both",
        max_dimension=1280,
        quality=80,
        test_double_image=test_image_1080p,
    )

    assert result.status == "success"
    assert result.raw_bytes is not None
    assert len(result.raw_bytes) > 0
    assert result.base64_data is not None

    meta = result.metadata
    assert meta.original_width == 1920
    assert meta.original_height == 1080
    assert meta.width == 1280
    assert meta.height == 720
    assert meta.mime_type == "image/jpeg"
    assert meta.byte_size == len(result.raw_bytes)
    assert len(meta.sha256_digest) == 64


def test_perception_downscaling_aspect_ratio(
    perception: ScreenPerception, test_image_1080p: Image.Image
) -> None:
    """Verify downscaling strictly preserves aspect ratio."""
    result = perception.capture(
        output_format="bytes",
        max_dimension=960,
        quality=75,
        test_double_image=test_image_1080p,
    )

    assert result.status == "success"
    assert result.metadata.width == 960
    assert result.metadata.height == 540


def test_perception_format_bytes_only(
    perception: ScreenPerception, test_image_1080p: Image.Image
) -> None:
    """Verify bytes-only format output."""
    result = perception.capture(
        output_format="bytes",
        test_double_image=test_image_1080p,
    )

    assert result.status == "success"
    assert result.raw_bytes is not None
    assert result.base64_data is None


def test_perception_format_base64_only(
    perception: ScreenPerception, test_image_1080p: Image.Image
) -> None:
    """Verify base64-only format output."""
    result = perception.capture(
        output_format="base64",
        test_double_image=test_image_1080p,
    )

    assert result.status == "success"
    assert result.base64_data is not None
    assert result.raw_bytes == b""


def test_perception_validate_image_bytes(perception: ScreenPerception) -> None:
    """Verify structural image validation detects corrupt buffers."""
    valid_buf = io.BytesIO()
    Image.new("RGB", (100, 100), color="red").save(valid_buf, format="JPEG")
    valid_bytes = valid_buf.getvalue()

    err = perception.validate_image_bytes(valid_bytes)
    assert err is None

    err_invalid = perception.validate_image_bytes(b"not-a-valid-image-buffer")
    assert err_invalid is not None
    assert "magic bytes" in err_invalid or "suspiciously small" in err_invalid
