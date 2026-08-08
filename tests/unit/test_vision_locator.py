"""Tests for the vision locator's output handling.

The network call is mocked throughout. What is under test is the part that
decides whether the model's reply becomes a highlight: a locator that silently
turns a malformed reply into a plausible-looking box would put a circle on the
wrong part of the screen, which is worse than drawing nothing.
"""

from unittest.mock import patch

import pytest

from src.vision_locator import build_prompt, locate_phrase, parse_box_response


class TestParseBoxResponse:
    def test_parses_a_well_formed_box(self):
        # box_2d is [ymin, xmin, ymax, xmax] normalised to 0-1000. On a
        # 1000x1000 image the numbers map one-to-one.
        box = parse_box_response('{"box_2d": [100, 200, 300, 400]}', 1000, 1000)
        assert box == {"left": 200, "top": 100, "width": 200, "height": 200}

    def test_scales_to_the_image(self):
        # Half the normalised range on a 1920x1080 image.
        box = parse_box_response('{"box_2d": [0, 0, 500, 500]}', 1920, 1080)
        assert box == {"left": 0, "top": 0, "width": 960, "height": 540}

    def test_coordinate_order_is_not_transposed(self):
        # A wide, short box must come back wide and short. Getting the y/x
        # order backwards still yields a valid-looking box, so this asserts
        # the orientation explicitly.
        box = parse_box_response('{"box_2d": [0, 0, 100, 900]}', 1000, 1000)
        assert box["width"] > box["height"]

    def test_strips_markdown_fences(self):
        raw = '```json\n{"box_2d": [10, 10, 20, 20]}\n```'
        assert parse_box_response(raw, 1000, 1000) is not None

    def test_reported_absent_returns_none(self):
        assert parse_box_response('{"found": false}', 1000, 1000) is None

    def test_non_json_returns_none(self):
        assert parse_box_response("I can see the phrase near the top.", 1000, 1000) is None

    def test_json_that_is_not_an_object_returns_none(self):
        assert parse_box_response("[100, 200, 300, 400]", 1000, 1000) is None

    def test_wrong_length_returns_none(self):
        assert parse_box_response('{"box_2d": [1, 2, 3]}', 1000, 1000) is None

    def test_non_numeric_coordinates_return_none(self):
        assert parse_box_response('{"box_2d": ["a", "b", "c", "d"]}', 1000, 1000) is None

    def test_inverted_box_returns_none(self):
        # ymax below ymin describes no region; accepting it would produce a
        # negative-extent box that silently scores zero later.
        assert parse_box_response('{"box_2d": [500, 100, 100, 400]}', 1000, 1000) is None

    def test_zero_extent_box_returns_none(self):
        assert parse_box_response('{"box_2d": [100, 100, 100, 400]}', 1000, 1000) is None

    def test_overshoot_is_clamped_to_the_image(self):
        # A box running past the edge is a rounding artefact, not a wrong
        # answer, so it is clamped rather than discarded.
        box = parse_box_response('{"box_2d": [-20, -20, 1200, 1200]}', 800, 600)
        assert box == {"left": 0, "top": 0, "width": 800, "height": 600}

    def test_degenerate_result_keeps_one_pixel(self):
        # Rounds to zero width on a small image, but the model did point
        # somewhere; a zero-width box would be scored as a miss.
        box = parse_box_response('{"box_2d": [0, 0, 1000, 1]}', 100, 100)
        assert box is not None
        assert box["width"] >= 1

    def test_rejects_non_positive_image_dimensions(self):
        with pytest.raises(ValueError, match="positive"):
            parse_box_response('{"box_2d": [0, 0, 10, 10]}', 0, 100)


class TestBuildPrompt:
    def test_includes_the_phrase(self):
        assert "count++" in build_prompt("count++")

    def test_states_the_coordinate_convention(self):
        # The convention is spelled out rather than relying on the model's
        # default, since a change to that default would transpose every box.
        prompt = build_prompt("anything")
        assert "ymin, xmin, ymax, xmax" in prompt
        assert "1000" in prompt

    def test_offers_an_explicit_not_found_reply(self):
        assert '{"found": false}' in build_prompt("anything")


class FakeImage:
    width = 1000
    height = 1000


class FakeUsage:
    prompt_token_count = 1234
    candidates_token_count = 56


class FakeResponse:
    usage_metadata = FakeUsage()


class TestLocatePhrase:
    def test_returns_box_and_usage(self):
        with (
            patch("src.vision_locator.generate_content", return_value=FakeResponse()),
            patch("src.vision_locator.response_text", return_value='{"box_2d": [0, 0, 100, 100]}'),
        ):
            result = locate_phrase(FakeImage(), "count++")

        assert result.box == {"left": 0, "top": 0, "width": 100, "height": 100}
        assert result.prompt_tokens == 1234
        assert result.response_tokens == 56

    def test_keeps_the_raw_reply_when_parsing_fails(self):
        # Without the raw text a malformed reply cannot be diagnosed without
        # paying for the call again.
        with (
            patch("src.vision_locator.generate_content", return_value=FakeResponse()),
            patch("src.vision_locator.response_text", return_value="somewhere near the top"),
        ):
            result = locate_phrase(FakeImage(), "count++")

        assert result.box is None
        assert result.raw == "somewhere near the top"

    def test_missing_usage_metadata_is_not_fatal(self):
        class NoUsage:
            pass

        with (
            patch("src.vision_locator.generate_content", return_value=NoUsage()),
            patch("src.vision_locator.response_text", return_value='{"box_2d": [0, 0, 10, 10]}'),
        ):
            result = locate_phrase(FakeImage(), "count++")

        assert result.box is not None
        assert result.prompt_tokens is None
