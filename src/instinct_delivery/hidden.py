"""Characters a person reviewing a rule file cannot see, but a model reads.

A rule's `action` is delivered to the model verbatim, so a hidden character in a
rule file is how instructions get smuggled past the review that "blessed" it (the
Rules File Backdoor class, Pillar Security, 2025-03-18). The classes are the ones
injection-screen's `invisible-unicode` detector uses (same author, Apache-2.0):
every format character (Unicode category Cf: zero-width characters, bidi
controls, the soft hyphen, the BOM), the whole tag block U+E0000-E007F (assigned
or not), every unassigned code point (category Cn), the blank-rendering code
points below, and variation selectors except a single U+FE0E/U+FE0F directly
after a visible character (not whitespace, not the start of the text, not
another selector). U+200D (ZWJ) is allowed only between two emoji, the left one
optionally followed by U+FE0F (emoji ZWJ sequences such as a family).
"""
from __future__ import annotations

import unicodedata

EXTRA_INVISIBLE = frozenset(chr(c) for c in (
    0x034F,                          # COMBINING GRAPHEME JOINER
    0x16FE4,                         # KHITAN SMALL SCRIPT FILLER
    0x115F, 0x1160, 0x3164, 0xFFA0,  # Hangul fillers
    0x17B4, 0x17B5,                  # Khmer inherent vowels
    0x180B, 0x180C, 0x180D, 0x180F,  # Mongolian free variation selectors
    0x2800,                          # BRAILLE PATTERN BLANK
))
_VS = (range(0xFE00, 0xFE10), range(0xE0100, 0xE01F0))
_TAGS = range(0xE0000, 0xE0080)          # the whole tag block, assigned or not
_PRESENTATION = ("\ufe0e", "\ufe0f")
_ZWJ = "\u200d"
# Emoji, approximated by the standard ranges (Extended_Pictographic is not in the stdlib).
_EMOJI = (range(0x1F000, 0x1FB00), range(0x2600, 0x27C0))


def _is_vs(c: str) -> bool:
    return any(ord(c) in r for r in _VS)


def _is_emoji(c: str) -> bool:
    return any(ord(c) in r for r in _EMOJI)


def _hidden_on_its_own(c: str) -> bool:
    return (unicodedata.category(c) in ("Cf", "Cn", "Co") or ord(c) in _TAGS
            or (unicodedata.category(c) == "Cc" and not c.isspace())
            or c in EXTRA_INVISIBLE or _is_vs(c))


def _visible(c: str) -> bool:
    """A character a reviewer sees as a mark: not whitespace, a control, or anything hidden."""
    return (not c.isspace() and unicodedata.category(c) not in ("Cc", "Cs", "Zs", "Zl", "Zp")
            and not _hidden_on_its_own(c))


def _zwj_joins_emoji(text: str, i: int) -> bool:
    """True when the ZWJ at `i` sits between two emoji: the left emoji may carry a
    following U+FE0F (a skin-tone modifier is itself in the emoji range)."""
    if i + 1 >= len(text) or not _is_emoji(text[i + 1]):
        return False
    left = text[i - 1] if i else ""
    if left == "\ufe0f":
        left = text[i - 2] if i >= 2 else ""
    return bool(left) and _is_emoji(left)


def first_hidden(text: str):
    """(index, character) of the first hidden character in `text`, or None."""
    for i, c in enumerate(text):
        if c in _PRESENTATION:
            if i and _visible(text[i - 1]):
                continue
            return i, c
        if c == _ZWJ and _zwj_joins_emoji(text, i):
            continue
        if _hidden_on_its_own(c):
            return i, c
    return None


def describe(c: str) -> str:
    name = unicodedata.name(c, "unnamed")
    return f"U+{ord(c):04X} {name}"


def printable(text: str) -> str:
    """``text`` with every control or other unprintable character spelled out, line breaks kept,
    for printing strings that came from elsewhere to a terminal."""
    return "".join(c if c.isprintable() or c == "\n" else c.encode("unicode_escape").decode() for c in text)
