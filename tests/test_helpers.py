from __future__ import annotations

from hermitcrab.utils.helpers import safe_filename


def test_safe_filename_sanitizes_unsafe_chars_and_dots() -> None:
    assert safe_filename("a/b\\c:d") == "a_b_c_d"
    assert safe_filename("") == "_"
    assert safe_filename(".") == "_"
    assert safe_filename("..") == "_"
    assert safe_filename(".hidden") == "hidden"
    assert safe_filename("  spaced  ") == "spaced"
