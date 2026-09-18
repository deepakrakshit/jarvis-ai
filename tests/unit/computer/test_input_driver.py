"""Unit tests for InputDriver."""

from __future__ import annotations

import pytest

from jarvis.computer.input_driver import InputDriver


@pytest.fixture
def input_driver() -> InputDriver:
    return InputDriver()


def test_input_driver_denormalize_coordinates(input_driver: InputDriver) -> None:
    """Verify coordinate denormalization from normalized [0, 999] scale."""
    metrics = input_driver.get_metrics()
    w = metrics.virtual_width
    h = metrics.virtual_height

    # Midpoint on 0-999 scale (500, 500)
    px, py = input_driver.denormalize(500, 500)
    assert abs(px - (w // 2)) <= 5
    assert abs(py - (h // 2)) <= 5

    # Origin (0, 0)
    px_0, py_0 = input_driver.denormalize(0, 0)
    assert px_0 >= 0
    assert py_0 >= 0


def test_input_driver_bounds_validation(input_driver: InputDriver) -> None:
    """Verify coordinate bounds validation accepts in-bounds and rejects out-of-bounds coordinates."""
    metrics = input_driver.get_metrics()
    # Midpoint is valid
    assert (
        input_driver.validate_coordinate_bounds(
            metrics.virtual_width // 2, metrics.virtual_height // 2
        )
        is True
    )

    # Negative coordinates outside virtual screen
    assert input_driver.validate_coordinate_bounds(-99999, -99999) is False

    # Outrageously large coordinates
    assert input_driver.validate_coordinate_bounds(999999, 999999) is False


def test_input_driver_click_out_of_bounds(input_driver: InputDriver) -> None:
    """Verify click operation on out-of-bounds coordinates raises ValueError."""
    with pytest.raises(ValueError, match="outside virtual screen"):
        input_driver.click(x=-5000, y=-5000)


def test_input_driver_move_out_of_bounds(input_driver: InputDriver) -> None:
    """Verify mouse move to out-of-bounds coordinates raises ValueError."""
    with pytest.raises(ValueError, match="outside virtual screen"):
        input_driver.move_mouse(x=888888, y=888888)
