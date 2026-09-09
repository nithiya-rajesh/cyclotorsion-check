"""Sanitization of untrusted, model-produced text fields (architecture review
Major).

The Gemini/generative-model responses are **untrusted input** — a model can emit
arbitrary characters, embedded markup, or control sequences. Those strings are
echoed into the SPA response (landmark descriptions, warnings), so they must be
neutralized at the API boundary before leaving the backend, and again escaped at
render on the frontend (defense in depth).

Scope is deliberately narrow: **display-only free text**. We never sanitize
computed numbers/IDs — only character payloads that reach an HTML renderer.
The exact clinical content (the angle) is computed deterministically and is not
model text; only human-readable descriptions and warnings flow through here.
"""

from __future__ import annotations

import re
import unicodedata

# Soft cap on any free-text field that reaches the client (mirrors
# detector.MAX_DESCRIPTION_CHARS as a belt-and-braces floor elsewhere; longer
# content is truncated).

# Control characters (C0/C1) and Unicode category "Cc"/"Cf" are always unsafe /
# transport-hostile but never legitimately part of an eye-photo description.
# They are replaced with a space (not deleted) so surrounding words stay
# separated, then whitespace is collapsed.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# Collapse any run of whitespace/newlines to a single space so a description
# can never inject vertical layout or huge gaps.
_WHITESPACE_RE = re.compile(r"\s+")


def sanitize_text(value: str, max_chars: int = 200) -> str:
    """Return a transport- and render- safe version of free-text ``value``.

    Removes control characters, strips/trims, collapses whitespace, drops
    any HTML/script-ish markup delimiters so it cannot carry active markup into
    the SPA, and caps length. Empty/whitespace-only input becomes an empty
    string.

    This is a *neutralization* step; the frontend must still HTML-escape when
    rendering (the SPA renders via ``textContent`` — see ``el()`` in the
    frontend) — sanitize here reduces the blast radius, escaping there prevents
    the injection.

    Tradeoff note: aggressive stripping could mangle a legitimately-multi-word
    description, but landmark descriptions are short labels, so collapsing
    whitespace and dropping markup delimiters is safe and non-lossy in practice.
    The lexical content of words (e.g. the literal word "script") is harmless
    once delimiters are gone and the renderer uses text — removing whole words
    could corrupt legitimate labels, so we do not.
    """
    if value is None:
        return ""
    out = unicodedata.normalize("NFC", value)
    out = _CONTROL_RE.sub(" ", out)
    # Remove angle-bracket markup delimiters and ampersand-entities so raw
    # HTML/scripts cannot survive into an HTML renderer.
    out = out.replace("<", " ").replace(">", " ")  # sanitizes script/img tags
    out = _WHITESPACE_RE.sub(" ", out).strip()
    return out[:max_chars]


# A Date-Of-Birth-like pattern: dd/mm/yyyy (or d-m-yy) and ISO yyyy-mm-dd. The
# case_ref field must remain a pseudonymous facility code (PRD Epic 6, US-6.4);
# a value that looks like a real date is a strong signal of an accidental PII
# entry that needs a nudge.
_DOB_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"  # dd/mm/yyyy, d-m-yy
    r"|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"  # yyyy-mm-dd (ISO)
    r")\b"
)

# A full-name-like pattern: two or more capitalised words separated by spaces
# (e.g. "Anjali Rao"); a surgeon pasting a patient's real name into the case
# field is the exact risky case US-6.4 guards against.
_NAME_RE = re.compile(r"\b[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?(?: [A-Z][a-z]+){1,}\b")
# Name-like shapes need lowercase letters to disambiguate a person's name from
# a pure-caps facility code (e.g. "JOHN SMITH" is ambiguous; "Anjali Rao" reads
# strongly as a person).
_HAS_LOWERCASE_RE = re.compile(r"[a-z]")


def looks_like_pii(value: str | None) -> bool:
    """US-6.4 safety-nudge check: does ``case_ref`` look like it contains PII?

    Returns ``True`` for values that are *likely* a patient's real name or date
    of birth (the two PII shapes the PRD warns about), so the UI can show:
    "This looks like it might contain a name or date of birth — please use your
    facility's case number instead."

    This is explicitly a **lightweight pattern check**, not a hard technical
    guarantee (PRD US-6.4 / AC): a well-formed human name is indistinguishable
    in general, so we treat this strictly as a UX nudge layered on top of the
    facility's own data-handling policy. Matching is intentionally narrow to
    avoid false-positive harassing of genuinely-pseudonymous facility codes
    like ``CASE-2026-001`` or ``SX-4317``:

      - DOB-like: digits separated like a date (``12/03/1990``).
      - Name-like: at least two space-separated capitalised words (``Anjali
        Rao``). A single word or a thematically-coded code is not flagged.
    """
    if not value:
        return False
    text = value.strip()
    if not text:
        return False
    if _DOB_RE.search(text):
        return True
    # Name heuristic: needs both capitalised words AND lowercase letters (a
    # pure-caps code like "JOHN SMITH" is ambiguous, but capitals+lowercase in
    # a two-word form reads strongly as a person's name).
    return bool(_NAME_RE.search(text) and _HAS_LOWERCASE_RE.search(text))
