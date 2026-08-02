"""Unit tests for the capture-image to overlay coordinate transform.

These are deterministic and import no Qt, PIL, or network code, so they run
anywhere including CI without a display.
"""

import pytest

from src.attention.coordinates import CoordinateMapper


class TestFit:
    def test_matching_sizes_produce_identity_transform(self):
        mapper = CoordinateMapper.fit(1920, 1080, 1920, 1080)

        assert mapper.scale == 1.0
        assert mapper.offset_x == 0
        assert mapper.offset_y == 0

    def test_scaled_display_shrinks_source_to_logical_pixels(self):
        # A 2560x1440 panel at 125% OS scaling reports 2048x1152 logical pixels.
        mapper = CoordinateMapper.fit(2560, 1440, 2048, 1152)

        assert mapper.scale == pytest.approx(0.8)
        assert mapper.offset_x == 0
        assert mapper.offset_y == 0

    def test_letterboxes_when_aspect_ratios_differ(self):
        # 16:9 source into a square target: scale is bound by width, and the
        # leftover vertical space is split evenly top and bottom.
        mapper = CoordinateMapper.fit(1920, 1080, 1000, 1000)

        assert mapper.scale == pytest.approx(1000 / 1920)
        # Bound by width, so there is no horizontal letterbox (modulo float noise).
        assert mapper.offset_x == pytest.approx(0, abs=1e-9)
        assert mapper.offset_y == pytest.approx((1000 - 1080 * (1000 / 1920)) / 2)

    def test_aspect_ratio_is_always_preserved(self):
        mapper = CoordinateMapper.fit(1920, 1080, 3000, 1000)
        source_aspect = 1920 / 1080

        _, _, width, height = mapper.source_rect_in_target()

        assert width / height == pytest.approx(source_aspect, rel=1e-3)

    @pytest.mark.parametrize(
        "args",
        [
            (0, 1080, 1920, 1080),
            (1920, 0, 1920, 1080),
            (1920, 1080, 0, 1080),
            (1920, 1080, 1920, 0),
            (-1920, 1080, 1920, 1080),
        ],
    )
    def test_rejects_non_positive_dimensions(self, args):
        # A zero dimension would divide by zero and silently pin every
        # highlight to the origin, which is worse than failing loudly.
        with pytest.raises(ValueError, match="must be positive"):
            CoordinateMapper.fit(*args)


class TestMapBox:
    def test_identity_mapper_leaves_box_unchanged(self):
        mapper = CoordinateMapper.fit(1920, 1080, 1920, 1080)
        box = {"left": 100, "top": 200, "width": 50, "height": 20}

        assert mapper.map_box(box) == box

    def test_scales_box_down_for_a_125_percent_display(self):
        mapper = CoordinateMapper.fit(2560, 1440, 2048, 1152)

        mapped = mapper.map_box({"left": 100, "top": 200, "width": 50, "height": 20})

        assert mapped == {"left": 80, "top": 160, "width": 40, "height": 16}

    def test_does_not_mutate_the_input_box(self):
        mapper = CoordinateMapper.fit(2560, 1440, 2048, 1152)
        box = {"left": 100, "top": 200, "width": 50, "height": 20}

        mapper.map_box(box)

        assert box == {"left": 100, "top": 200, "width": 50, "height": 20}

    def test_missing_key_names_the_offending_field(self):
        mapper = CoordinateMapper.fit(1920, 1080, 1920, 1080)

        with pytest.raises(KeyError, match="height"):
            mapper.map_box({"left": 0, "top": 0, "width": 10})

    def test_bottom_right_box_stays_inside_the_target(self):
        # The regression this whole module exists for: on a scaled display the
        # untransformed box would land past the edge of the overlay and the
        # highlight would be clipped away entirely.
        mapper = CoordinateMapper.fit(3840, 2160, 2560, 1440)
        box = {"left": 3800, "top": 2140, "width": 30, "height": 15}

        mapped = mapper.map_box(box)

        assert mapped["left"] + mapped["width"] <= 2560
        assert mapped["top"] + mapped["height"] <= 1440


class TestMapLength:
    def test_never_collapses_a_real_box_to_zero(self):
        # Aggressive downscale: a 1px source feature must still be drawable.
        mapper = CoordinateMapper.fit(4000, 4000, 100, 100)

        assert mapper.map_length(1) == 1

    def test_scales_proportionally(self):
        mapper = CoordinateMapper.fit(1000, 1000, 500, 500)

        assert mapper.map_length(100) == 50


class TestSourceRectInTarget:
    def test_fills_target_exactly_when_aspect_matches(self):
        mapper = CoordinateMapper.fit(2560, 1440, 1280, 720)

        assert mapper.source_rect_in_target() == (0, 0, 1280, 720)

    def test_is_centred_when_letterboxed(self):
        mapper = CoordinateMapper.fit(1000, 500, 1000, 1000)
        left, top, width, height = mapper.source_rect_in_target()

        assert left == 0
        assert width == 1000
        assert top == pytest.approx(250, abs=1)
        assert height == 500


class TestImmutability:
    def test_mapper_is_frozen(self):
        mapper = CoordinateMapper.identity()

        with pytest.raises(Exception):
            mapper.scale = 2.0  # type: ignore[misc]
