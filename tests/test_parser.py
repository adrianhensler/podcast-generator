import pytest
from app.services.script_generator import parse_script_lines, ScriptParseError


def test_basic_two_hosts():
    script = "Host A: Hello there.\nHost B: Hi, welcome back."
    lines = parse_script_lines(script)
    assert len(lines) == 2
    assert lines[0].speaker == "Host A"
    assert lines[0].text == "Hello there."
    assert lines[1].speaker == "Host B"
    assert lines[1].text == "Hi, welcome back."


def test_leading_whitespace():
    script = "  Host A: Indented line.\n  Host B: Also indented."
    lines = parse_script_lines(script)
    assert len(lines) == 2
    assert lines[0].text == "Indented line."


def test_trailing_whitespace_stripped():
    script = "Host A: Some text with trailing spaces.   \nHost B: Another line."
    lines = parse_script_lines(script)
    assert lines[0].text == "Some text with trailing spaces."


def test_non_host_lines_ignored():
    script = """This is a preamble.
Host A: First line.
Some narrator text here.
Host B: Second line.
End of script."""
    lines = parse_script_lines(script)
    assert len(lines) == 2


def test_empty_script_raises():
    with pytest.raises(ScriptParseError):
        parse_script_lines("")


def test_no_host_lines_raises():
    with pytest.raises(ScriptParseError) as exc_info:
        parse_script_lines("This script has no host lines.\nJust plain text.")
    assert "No Host A:/Host B: lines found" in str(exc_info.value)


def test_single_host_only():
    script = "Host A: Line one.\nHost A: Line two.\nHost A: Line three."
    lines = parse_script_lines(script)
    assert all(l.speaker == "Host A" for l in lines)
    assert len(lines) == 3


def test_long_line_truncation():
    long_text = "x" * 10000
    script = f"Host A: {long_text}"
    lines = parse_script_lines(script)
    assert len(lines[0].text) == 9800


def test_multiline_script():
    script = """Host A: Welcome to the show. Today we're talking about something important.
Host B: That's right, and I have a lot to say about it.
Host A: Let's dive in.
Host B: The first point is that everything is connected."""
    lines = parse_script_lines(script)
    assert len(lines) == 4


def test_colon_in_text():
    script = "Host A: Here's a key ratio: 3 to 1.\nHost B: Interesting point."
    lines = parse_script_lines(script)
    assert lines[0].text == "Here's a key ratio: 3 to 1."


def test_parse_error_shows_preview():
    bad_script = "No hosts here at all. Just random text."
    with pytest.raises(ScriptParseError) as exc_info:
        parse_script_lines(bad_script)
    assert "Preview" in str(exc_info.value)
