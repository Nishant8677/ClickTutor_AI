"""Unit tests for anchor grounding and repair.

The model sometimes returns an anchor drawn from its knowledge of the topic
rather than from the screen -- measured live, "in-place" for a LeetCode
problem whose visible text never contains the word. Such a step renders an
explanation with nothing highlighted. Naming the rejected phrase and asking
again resolves it.

Gemini is mocked at the boundary, so these are deterministic and free.
"""

import pytest

import src.lesson_engine as engine_module
from src.lesson_engine import LessonEngine

# Two lines of OCR output; "in-place" appears in neither.
OCR_DATA = {
    "text": ["modify", "the", "input", "matrix", "directly", "Example", "1"],
    "left": [10, 60, 90, 140, 200, 10, 70],
    "top": [10, 10, 10, 10, 10, 40, 40],
    "width": [45, 25, 45, 55, 70, 60, 10],
    "height": [12, 12, 12, 12, 12, 12, 12],
    "conf": [96.0] * 7,
    "block_num": [1] * 7,
    "par_num": [1] * 7,
    "line_num": [1, 1, 1, 1, 1, 2, 2],
    "_scale": 1,
}


def step(anchor, explanation="why it matters"):
    return {
        "step": 1,
        "title": "t",
        "anchor": anchor,
        "context": None,
        "attention": "circle",
        "emphasis": "high",
        "explanation": explanation,
    }


@pytest.fixture
def calls(monkeypatch):
    """Records repair calls and lets a test script the replies."""
    record = {"prompts": [], "replies": []}

    def fake_generate(parts, *a, **kw):
        record["prompts"].append(parts[0])
        return object()

    def fake_response_text(_):
        return record["replies"].pop(0)

    monkeypatch.setattr(engine_module, "generate_content", fake_generate)
    monkeypatch.setattr(engine_module, "response_text", fake_response_text)
    return record


@pytest.fixture
def engine():
    return LessonEngine("unused.png", OCR_DATA)


class TestVisibleTextBlock:
    def test_quotes_the_ocr_lines(self, engine):
        block = engine.visible_text_block()

        assert "modify the input matrix directly" in block

    def test_empty_when_ocr_found_nothing(self):
        assert LessonEngine("x.png", {"text": [], "_scale": 1}).visible_text_block() == ""

    def test_respects_the_line_cap(self, engine):
        assert len(engine.visible_text_block(max_lines=1).splitlines()) == 1

    def test_respects_the_character_budget(self, engine):
        assert len(engine.visible_text_block(max_chars=5)) <= 5


class TestRepairAnchors:
    def test_locatable_anchors_are_left_alone(self, engine, calls):
        result = engine.repair_anchors([step("modify")], image=None)

        assert result[0]["anchor"] == "modify"
        assert calls["prompts"] == []  # no API call for a working anchor

    def test_none_anchors_are_skipped(self, engine, calls):
        result = engine.repair_anchors([step("NONE")], image=None)

        assert result[0]["anchor"] == "NONE"
        assert calls["prompts"] == []

    def test_unlocatable_anchor_is_replaced(self, engine, calls):
        calls["replies"].append("modify the input matrix directly")

        result = engine.repair_anchors([step("in-place")], image=None)

        assert result[0]["anchor"] == "modify the input matrix directly"

    def test_names_the_rejected_phrase_in_the_prompt(self, engine, calls):
        calls["replies"].append("modify")

        engine.repair_anchors([step("in-place")], image=None)

        assert "in-place" in calls["prompts"][0]
        assert "modify the input matrix directly" in calls["prompts"][0]

    def test_context_is_cleared_on_replacement(self, engine, calls):
        # The replacement is quoted from one line, so the original
        # disambiguating context no longer describes it.
        calls["replies"].append("modify")
        original = step("in-place")
        original["context"] = "some other line"

        result = engine.repair_anchors([original], image=None)

        assert result[0]["context"] is None

    def test_strips_quotes_the_model_wraps_around_its_reply(self, engine, calls):
        calls["replies"].append('"modify"')

        result = engine.repair_anchors([step("in-place")], image=None)

        assert result[0]["anchor"] == "modify"

    def test_step_is_untouched_when_replacement_also_fails(self, engine, calls):
        calls["replies"].append("still not on screen")

        result = engine.repair_anchors([step("in-place")], image=None)

        assert result[0]["anchor"] == "in-place"

    def test_step_is_untouched_when_the_call_raises(self, engine, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(engine_module, "generate_content", boom)

        result = engine.repair_anchors([step("in-place")], image=None)

        assert result[0]["anchor"] == "in-place"

    def test_repairs_are_capped(self, engine, calls):
        calls["replies"].extend(["modify"] * 5)
        steps = [step("in-place") for _ in range(5)]

        engine.repair_anchors(steps, image=None, max_repairs=2)

        assert len(calls["prompts"]) == 2

    def test_no_calls_when_ocr_is_empty(self, calls):
        blind = LessonEngine("x.png", {"text": [], "_scale": 1})

        result = blind.repair_anchors([step("anything")], image=None)

        assert calls["prompts"] == []
        assert result[0]["anchor"] == "anything"

    def test_every_step_is_returned(self, engine, calls):
        calls["replies"].append("modify")
        steps = [step("modify"), step("in-place"), step("NONE")]

        assert len(engine.repair_anchors(steps, image=None)) == 3
