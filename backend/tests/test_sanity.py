"""Unit tests for the sanity validation engine (PRD US-3.1)."""

from cyclotorsion.sanity import (
    FLAG_NEAR_ZERO,
    FLAG_TOO_LARGE,
    evaluate_angle,
    human_message,
)


def test_in_range_passes():
    result = evaluate_angle(5.0)
    assert result.passed is True
    assert result.flags == ()


def test_too_large_positive():
    result = evaluate_angle(21.0)
    assert result.passed is False
    assert FLAG_TOO_LARGE in result.flags


def test_too_large_negative():
    result = evaluate_angle(-45.0)
    assert result.passed is False
    assert FLAG_TOO_LARGE in result.flags


def test_near_zero_positive():
    result = evaluate_angle(0.05)
    assert result.passed is False
    assert FLAG_NEAR_ZERO in result.flags


def test_boundary_at_20_is_pass():
    # US-3.1: >20 flags; exactly 20 is allowed.
    assert evaluate_angle(20.0).passed is True
    assert evaluate_angle(-20.0).passed is True


def test_boundary_at_0_1_is_pass():
    # US-3.1: <0.1 flags; exactly 0.1 is allowed.
    assert evaluate_angle(0.1).passed is True


def test_human_messages():
    assert "unusually large" in human_message((FLAG_TOO_LARGE,))
    assert "near zero" in human_message((FLAG_NEAR_ZERO,))
    assert human_message(()) is None
