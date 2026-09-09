"""Tests for untrusted free-text sanitization (architecture review Major)."""

from cyclotorsion.text import looks_like_pii, sanitize_text


def test_strips_markup_delimiters():
    out = sanitize_text('<script>alert("x")</script>bright marker')
    # Angle-bracket delimiters are removed so no active markup survives into an
    # HTML renderer; remaining lexical content is inert (rendered via textContent).
    assert "<" not in out and ">" not in out
    assert "bright marker" in out


def test_removes_control_characters_as_spaces():
    out = sanitize_text("bright\x00marker\x1fcorner")
    assert "\x00" not in out and "\x1f" not in out
    assert out == "bright marker corner"


def test_collapses_whitespace_and_trims():
    out = sanitize_text("   bright   \n\t  marker  ")
    assert out == "bright marker"


def test_caps_length():
    out = sanitize_text("a" * 500, max_chars=200)
    assert len(out) == 200


def test_none_and_empty():
    assert sanitize_text(None) == ""
    assert sanitize_text("") == ""
    assert sanitize_text("   ") == ""


def test_looks_like_pii_flags_two_capitalised_words():
    # A surgeon pasting a patient's real name (US-6.4) into the case field.
    assert looks_like_pii("Anjali Rao") is True
    assert looks_like_pii("Anjali Devi Rao") is True
    assert looks_like_pii("Samuel John") is True


def test_looks_like_pii_flags_date_of_birth_patterns():
    assert looks_like_pii("12/03/1990") is True
    assert looks_like_pii("1985-07-11") is True
    assert looks_like_pii("CASE 07/11/1985") is True


def test_looks_like_pii_leaves_facility_codes_alone():
    # Pseudonymous but factual facility-generated codes must NOT be nagged.
    assert looks_like_pii("CASE-2026-001") is False
    assert looks_like_pii("SX-4317") is False
    assert looks_like_pii("cs45") is False
    assert looks_like_pii("") is False
    assert looks_like_pii(None) is False
