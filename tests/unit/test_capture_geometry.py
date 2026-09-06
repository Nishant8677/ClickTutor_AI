"""Area-selection geometry: CaptureGeometry, the crop mapper, and the overlay.

The geometry and mapper tests import no Qt. The overlay tests run under the
offscreen platform plugin and never capture a screen.
"""

from __future__ import annotations

import dataclasses
import math

import pytest
from PIL import Image

from src.attention.coordinates import CaptureGeometry, CoordinateMapper, validate_region

SCREEN = {"left": 0, "top": 0, "width": 1920, "height": 1080}


def _selection(x0, y0, x1, y1, ratio=1.0, screen=SCREEN):
    return CaptureGeometry.from_logical_selection(x0, y0, x1, y1, screen, ratio)


class TestFromLogicalSelection:
    def test_dpr_1_maps_logical_to_physical_one_to_one(self):
        geometry = CaptureGeometry.from_logical_rect(100, 80, 400, 200, SCREEN, 1.0)
        assert geometry.region() == {"left": 100, "top": 80, "width": 400, "height": 200}
        assert (geometry.origin_dx, geometry.origin_dy) == (100, 80)

    def test_dpr_1_5_scales_size_and_origin(self):
        geometry = CaptureGeometry.from_logical_rect(100, 80, 400, 200, SCREEN, 1.5)
        assert geometry.region() == {"left": 150, "top": 120, "width": 600, "height": 300}
        assert geometry.device_pixel_ratio == 1.5

    def test_reversed_drag_gives_same_rectangle(self):
        forward = _selection(100, 80, 500, 280)
        backward = _selection(500, 280, 100, 80)
        assert backward == forward

    def test_fractional_edges_floor_leading_and_ceil_trailing(self):
        geometry = _selection(10.2, 20.7, 30.1, 40.4, ratio=1.0)
        assert geometry.region() == {"left": 10, "top": 20, "width": 21, "height": 21}

    def test_fractional_physical_edges_after_scaling_never_trim_a_pixel(self):
        # 10.5 * 1.25 = 13.125 -> 13 ; 30.5 * 1.25 = 38.125 -> 39
        geometry = _selection(10.5, 10.5, 30.5, 30.5, ratio=1.25)
        assert geometry.region() == {"left": 13, "top": 13, "width": 26, "height": 26}

    def test_clamps_to_screen_bounds(self):
        geometry = _selection(-50, -50, 100, 100)
        assert geometry.region() == {"left": 0, "top": 0, "width": 100, "height": 100}
        geometry = _selection(1900, 1000, 5000, 5000)
        assert geometry.region() == {"left": 1900, "top": 1000, "width": 20, "height": 80}

    def test_entirely_off_screen_is_rejected(self):
        with pytest.raises(ValueError, match="no area"):
            _selection(2000, 100, 2100, 200)

    @pytest.mark.parametrize("ratio", [1.0, 1.5])
    def test_equal_fractional_endpoints_are_rejected_not_rounded_apart(self, ratio):
        with pytest.raises(ValueError, match="no area"):
            _selection(10.5, 10, 10.5, 50, ratio=ratio)
        with pytest.raises(ValueError, match="no area"):
            _selection(10, 20.25, 50, 20.25, ratio=ratio)

    def test_zero_size_rect_is_rejected(self):
        with pytest.raises(ValueError, match="no area"):
            CaptureGeometry.from_logical_rect(100, 80, 0, 200, SCREEN, 1.0)

    @pytest.mark.parametrize("ratio", [0, -1.0, math.nan, math.inf, True])
    def test_rejects_invalid_ratio(self, ratio):
        with pytest.raises(ValueError):
            _selection(0, 0, 10, 10, ratio=ratio)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, "10"])
    def test_rejects_non_finite_coordinates(self, bad):
        with pytest.raises(ValueError):
            _selection(bad, 0, 10, 10)

    def test_negative_screen_origin_is_subtracted_before_overlay_mapping(self):
        screen = {"left": -1920, "top": -200, "width": 1920, "height": 1080}
        geometry = CaptureGeometry.from_logical_rect(100, 80, 400, 200, screen, 1.0)
        assert geometry.region() == {"left": -1820, "top": -120, "width": 400, "height": 200}
        assert (geometry.origin_dx, geometry.origin_dy) == (100, 80)

    def test_nonzero_positive_screen_origin(self):
        screen = {"left": 1920, "top": 0, "width": 1920, "height": 1080}
        geometry = CaptureGeometry.from_logical_rect(10, 20, 30, 40, screen, 2.0)
        assert geometry.region() == {"left": 1940, "top": 40, "width": 60, "height": 80}
        assert (geometry.origin_dx, geometry.origin_dy) == (20, 40)

    def test_malformed_screen_region_is_rejected(self):
        with pytest.raises(ValueError, match="screen_region"):
            _selection(0, 0, 10, 10, screen={"left": 0, "top": 0})


class TestCaptureGeometryValue:
    def test_is_frozen(self):
        geometry = _selection(0, 0, 10, 10)
        with pytest.raises(dataclasses.FrozenInstanceError):
            geometry.width = 5  # type: ignore[misc]

    def test_region_returns_a_fresh_dict_each_call(self):
        geometry = _selection(0, 0, 10, 10)
        first = geometry.region()
        first["left"] = 999
        assert geometry.region()["left"] == 0
        assert geometry.region() is not first

    def test_logical_size_scales_by_inverse_ratio(self):
        geometry = CaptureGeometry.from_logical_rect(0, 0, 400, 200, SCREEN, 1.5)
        assert geometry.logical_width == pytest.approx(400)
        assert geometry.logical_height == pytest.approx(200)

    def test_matches_image_size(self):
        geometry = _selection(0, 0, 400, 200)
        assert geometry.matches_image_size(400, 200)
        assert not geometry.matches_image_size(200, 400)

    @pytest.mark.parametrize(
        "field, value",
        [
            ("left", 10.5),
            ("top", math.nan),
            ("width", math.inf),
            ("origin_dx", 1.25),
            ("origin_dy", "3"),
            ("height", True),
        ],
    )
    def test_direct_construction_rejects_nonintegral_physical_fields(self, field, value):
        kwargs = dict(left=0, top=0, width=10, height=10, origin_dx=0, origin_dy=0)
        kwargs[field] = value
        with pytest.raises(ValueError, match=field):
            CaptureGeometry(device_pixel_ratio=1.0, **kwargs)

    def test_direct_construction_rejects_invalid_dimensions(self):
        with pytest.raises(ValueError, match="positive"):
            CaptureGeometry(0, 0, 0, 10, 0, 0, 1.0)
        with pytest.raises(ValueError, match="positive"):
            CaptureGeometry(0, 0, 10, -1, 0, 0, 1.0)

    def test_direct_construction_coerces_whole_floats_to_int(self):
        geometry = CaptureGeometry(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 1.0)
        assert geometry.region() == {"left": 1, "top": 2, "width": 3, "height": 4}
        assert all(isinstance(v, int) for v in geometry.region().values())


class TestValidateRegion:
    def test_returns_an_int_copy(self):
        region = {"left": 1.0, "top": 2, "width": 3, "height": 4}
        assert validate_region(region) == {"left": 1, "top": 2, "width": 3, "height": 4}
        assert validate_region(region) is not region

    @pytest.mark.parametrize(
        "region",
        [
            {},
            None,
            {"left": 0, "top": 0, "width": 10},
            {"left": 0, "top": 0, "width": 0, "height": 10},
            {"left": 0, "top": 0, "width": 10, "height": -1},
            {"left": 0.5, "top": 0, "width": 10, "height": 10},
            {"left": math.nan, "top": 0, "width": 10, "height": 10},
            {"left": True, "top": 0, "width": 10, "height": 10},
        ],
    )
    def test_rejects_malformed_regions(self, region):
        with pytest.raises(ValueError):
            validate_region(region)


class TestCropMapper:
    def test_dpr_1_places_source_point_at_crop_origin_plus_offset(self):
        geometry = CaptureGeometry.from_logical_rect(100, 80, 400, 200, SCREEN, 1.0)
        mapper = CoordinateMapper.crop(geometry, 1920, 1080)
        assert mapper.map_point(30, 45) == (130, 125)
        assert mapper.map_length(100) == 100

    def test_dpr_1_5_divides_by_ratio(self):
        geometry = CaptureGeometry.from_logical_rect(100, 80, 400, 200, SCREEN, 1.5)
        mapper = CoordinateMapper.crop(geometry, 1280, 720)
        assert mapper.map_point(30, 45) == (120, 110)
        assert mapper.map_length(150) == 100

    def test_never_scales_to_fit_the_overlay(self):
        geometry = CaptureGeometry.from_logical_rect(0, 0, 100, 100, SCREEN, 1.0)
        mapper = CoordinateMapper.crop(geometry, 1920, 1080)
        assert mapper.scale == 1.0
        assert mapper.source_rect_in_target() == (0, 0, 100, 100)

    def test_crop_corner_lands_where_selected(self):
        geometry = CaptureGeometry.from_logical_rect(100, 80, 400, 200, SCREEN, 2.0)
        mapper = CoordinateMapper.crop(geometry, 960, 540)
        assert mapper.source_rect_in_target() == (100, 80, 400, 200)

    def test_uses_snapshot_ratio_not_a_new_one(self):
        # Geometry captured at 1.5 is mapped at 1.5 regardless of current DPR.
        geometry = CaptureGeometry.from_logical_rect(0, 0, 300, 300, SCREEN, 1.5)
        mapper = CoordinateMapper.crop(geometry, 100, 100)
        assert mapper.scale == pytest.approx(1 / 1.5)

    def test_rejects_non_positive_target(self):
        geometry = _selection(0, 0, 10, 10)
        with pytest.raises(ValueError):
            CoordinateMapper.crop(geometry, 0, 10)

    def test_fit_is_unchanged_by_crop_mode(self):
        mapper = CoordinateMapper.fit(400, 200, 1920, 1080)
        assert mapper.scale == pytest.approx(4.8)


@pytest.fixture(scope="module")
def qt_app():
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture
def overlay(qt_app):
    from src.attention.overlay import TransparentOverlay

    widget = TransparentOverlay()
    widget.resize(1920, 1080)
    yield widget
    widget.close()


def _crop_image(geometry):
    return Image.new("RGB", (geometry.width, geometry.height), (1, 2, 3))


class TestOverlayCaptureGeometry:
    def test_set_background_with_geometry_uses_crop_mapper(self, overlay):
        geometry = CaptureGeometry.from_logical_rect(100, 80, 400, 200, SCREEN, 1.0)
        overlay.set_background(_crop_image(geometry), show=False, capture_geometry=geometry)
        assert overlay.capture_geometry == geometry
        assert overlay.mapper.map_point(30, 45) == (130, 125)

    def test_geometry_survives_set_source_size_and_resize(self, overlay):
        geometry = CaptureGeometry.from_logical_rect(100, 80, 400, 200, SCREEN, 1.5)
        overlay.set_background(_crop_image(geometry), show=False, capture_geometry=geometry)
        overlay.set_source_size(geometry.width, geometry.height)
        overlay.resize(1280, 720)
        assert overlay.capture_geometry == geometry
        assert overlay.mapper.map_point(30, 45) == (120, 110)

    def test_set_source_size_contradicting_geometry_is_rejected(self, overlay):
        geometry = CaptureGeometry.from_logical_rect(100, 80, 400, 200, SCREEN, 1.0)
        overlay.set_background(_crop_image(geometry), show=False, capture_geometry=geometry)
        with pytest.raises(ValueError, match="does not match"):
            overlay.set_source_size(1920, 1080)
        assert overlay.capture_geometry == geometry

    def test_plain_set_background_resets_to_fit_mode(self, overlay):
        geometry = CaptureGeometry.from_logical_rect(100, 80, 400, 200, SCREEN, 1.0)
        overlay.set_background(_crop_image(geometry), show=False, capture_geometry=geometry)
        overlay.set_background(Image.new("RGB", (1920, 1080)), show=False)
        assert overlay.capture_geometry is None
        assert overlay.mapper == CoordinateMapper.fit(1920, 1080, 1920, 1080)

    def test_clear_drops_geometry(self, overlay):
        geometry = CaptureGeometry.from_logical_rect(100, 80, 400, 200, SCREEN, 1.0)
        overlay.set_background(_crop_image(geometry), show=False, capture_geometry=geometry)
        overlay.clear()
        assert overlay.capture_geometry is None
        # Source size is retained by clear(); only the crop placement goes.
        assert overlay.mapper == CoordinateMapper.fit(400, 200, 1920, 1080)

    def test_mismatched_image_leaves_all_state_untouched(self, overlay):
        first = CaptureGeometry.from_logical_rect(100, 80, 400, 200, SCREEN, 1.0)
        overlay.set_background(_crop_image(first), show=False, capture_geometry=first)
        before = (overlay.bg_pixmap, overlay.mapper, overlay.capture_geometry)

        second = CaptureGeometry.from_logical_rect(0, 0, 50, 50, SCREEN, 1.0)
        with pytest.raises(ValueError, match="does not match"):
            overlay.set_background(Image.new("RGB", (60, 60)), show=False, capture_geometry=second)

        assert (overlay.bg_pixmap, overlay.mapper, overlay.capture_geometry) == before
        assert overlay.mapper.map_point(30, 45) == (130, 125)

    def test_failed_load_with_geometry_leaves_state_untouched(self, overlay, tmp_path):
        geometry = CaptureGeometry.from_logical_rect(0, 0, 50, 50, SCREEN, 1.0)
        overlay.set_background(Image.new("RGB", (1920, 1080)), show=False)
        before = (overlay.bg_pixmap, overlay.mapper, overlay.capture_geometry)

        with pytest.raises(ValueError, match="failed to load"):
            overlay.set_background(
                str(tmp_path / "missing.png"), show=False, capture_geometry=geometry
            )
        assert (overlay.bg_pixmap, overlay.mapper, overlay.capture_geometry) == before

    def test_geometry_without_new_image_must_match_existing_source(self, overlay):
        overlay.set_background(Image.new("RGB", (400, 200)), show=False)
        wrong = CaptureGeometry.from_logical_rect(0, 0, 50, 50, SCREEN, 1.0)
        with pytest.raises(ValueError, match="does not match"):
            overlay.set_background(None, show=False, capture_geometry=wrong)
        assert overlay.capture_geometry is None

        right = CaptureGeometry.from_logical_rect(100, 80, 400, 200, SCREEN, 1.0)
        overlay.set_background(None, show=False, capture_geometry=right)
        assert overlay.capture_geometry == right
        assert overlay.mapper.map_point(30, 45) == (130, 125)

    def test_geometry_without_any_source_is_rejected(self, overlay):
        geometry = CaptureGeometry.from_logical_rect(0, 0, 50, 50, SCREEN, 1.0)
        with pytest.raises(ValueError, match="no source image"):
            overlay.set_background(None, show=False, capture_geometry=geometry)
        assert overlay.capture_geometry is None
