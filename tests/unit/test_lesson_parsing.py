"""Unit tests for parsing Gemini's lesson format.

Pure text in, structured steps out -- no network and no API key required,
because the model client is now built lazily.
"""

import pytest

from src.lesson_engine import parse_lesson_steps
from src.lesson_validator import validate_lesson_steps

WELL_FORMED = """
STEP 1
TITLE: Initialising the counter
ANCHOR: int count = 0
CONTEXT: for loop setup
ATTENTION: circle
EMPHASIS: high
EXPLANATION: This variable holds how many items we have seen so far.

STEP 2
TITLE: Incrementing
ANCHOR: count++
CONTEXT: NONE
ATTENTION: underline
EMPHASIS: low
EXPLANATION: Each pass through the loop bumps the counter by one.
"""


class TestWellFormedInput:
    def test_parses_every_step(self):
        steps = parse_lesson_steps(WELL_FORMED)

        assert [s["step"] for s in steps] == [1, 2]

    def test_extracts_all_fields(self):
        first = parse_lesson_steps(WELL_FORMED)[0]

        assert first["title"] == "Initialising the counter"
        assert first["anchor"] == "int count = 0"
        assert first["context"] == "for loop setup"
        assert first["attention"] == "circle"
        assert first["emphasis"] == "high"
        assert first["explanation"].startswith("This variable holds")

    def test_literal_none_context_becomes_python_none(self):
        # "NONE" means the anchor is unambiguous, not that the text is "NONE".
        second = parse_lesson_steps(WELL_FORMED)[1]

        assert second["context"] is None

    def test_output_satisfies_the_validator(self):
        is_valid, errors = validate_lesson_steps(parse_lesson_steps(WELL_FORMED))

        assert is_valid, errors


class TestMalformedInput:
    def test_returns_empty_list_for_unparseable_text(self):
        assert parse_lesson_steps("I'm sorry, I can't help with that.") == []

    def test_step_without_explanation_is_dropped_and_logged(self, caplog):
        # It is dropped either way; the point is that it stops being silent,
        # because a half-length lesson looked identical to a correct one.
        text = "STEP 1\nTITLE: Orphan\nANCHOR: foo\nATTENTION: circle\n"

        with caplog.at_level("WARNING"):
            steps = parse_lesson_steps(text)

        assert steps == []
        assert "no EXPLANATION" in caplog.text

    def test_missing_optional_fields_fall_back_to_defaults(self):
        text = "STEP 3\nEXPLANATION: Only the explanation survived.\n"

        step = parse_lesson_steps(text)[0]

        assert step["title"] == "Step 3"
        assert step["anchor"] == "NONE"
        assert step["attention"] == "none"
        assert step["emphasis"] == "low"


class TestEnumCoercion:
    @pytest.mark.parametrize(
        "supplied, expected",
        [
            ("CIRCLE", "circle"),
            ("  underline  ", "underline"),
            ("draw a big red blob", "none"),
            ("highlight", "none"),
        ],
    )
    def test_attention_is_normalised_to_known_vocabulary(self, supplied, expected):
        # An unrecognised value used to reach the renderer, whose else-branch
        # silently drew a rectangle.
        text = f"STEP 1\nANCHOR: x\nATTENTION: {supplied}\nEXPLANATION: e\n"

        assert parse_lesson_steps(text)[0]["attention"] == expected

    @pytest.mark.parametrize(
        "supplied, expected",
        [("HIGH", "high"), ("Medium", "medium"), ("critical", "low")],
    )
    def test_emphasis_is_normalised_to_known_vocabulary(self, supplied, expected):
        text = f"STEP 1\nANCHOR: x\nEMPHASIS: {supplied}\nEXPLANATION: e\n"

        assert parse_lesson_steps(text)[0]["emphasis"] == expected

    def test_unrecognised_value_is_logged(self, caplog):
        text = "STEP 1\nANCHOR: x\nATTENTION: sparkles\nEXPLANATION: e\n"

        with caplog.at_level("WARNING"):
            parse_lesson_steps(text)

        assert "sparkles" in caplog.text
