"""Tests for box comparison.

These underpin the locator experiment: if IoU is wrong, the comparison between
the OCR and vision locators is wrong in a way no amount of re-running would
reveal.
"""

import pytest

from src.box_metrics import area, centre_distance, iou


def box(left, top, width, height):
    return {"left": left, "top": top, "width": width, "height": height}


class TestIou:
    def test_identical_boxes_score_one(self):
        b = box(10, 20, 30, 40)
        assert iou(b, b) == pytest.approx(1.0)

    def test_disjoint_boxes_score_zero(self):
        assert iou(box(0, 0, 10, 10), box(100, 100, 10, 10)) == 0.0

    def test_boxes_touching_at_an_edge_do_not_overlap(self):
        # Sharing a boundary is not intersection; a highlight adjacent to the
        # target is a miss, not a partial hit.
        assert iou(box(0, 0, 10, 10), box(10, 0, 10, 10)) == 0.0

    def test_half_overlap(self):
        # Two 10x10 boxes offset by 5 on one axis: intersection 50,
        # union 100 + 100 - 50 = 150.
        assert iou(box(0, 0, 10, 10), box(5, 0, 10, 10)) == pytest.approx(50 / 150)

    def test_contained_box_scores_ratio_of_areas(self):
        # A 5x5 box inside a 10x10 box: intersection 25, union 100.
        assert iou(box(2, 2, 5, 5), box(0, 0, 10, 10)) == pytest.approx(0.25)

    def test_is_symmetric(self):
        a, b = box(0, 0, 10, 10), box(3, 4, 12, 6)
        assert iou(a, b) == pytest.approx(iou(b, a))

    def test_zero_area_box_scores_zero_rather_than_dividing_by_zero(self):
        # A locator returning a degenerate box is a result to record, not a
        # crash mid-experiment.
        assert iou(box(0, 0, 0, 10), box(0, 0, 10, 10)) == 0.0

    def test_negative_extent_is_treated_as_empty(self):
        assert iou(box(0, 0, -5, 10), box(0, 0, 10, 10)) == 0.0

    def test_accepts_float_coordinates(self):
        assert iou(box(0.0, 0.0, 10.5, 10.5), box(0.0, 0.0, 10.5, 10.5)) == pytest.approx(1.0)

    def test_missing_key_names_the_offender(self):
        with pytest.raises(KeyError, match="height"):
            iou({"left": 0, "top": 0, "width": 10}, box(0, 0, 10, 10))

    def test_missing_key_says_which_box_was_wrong(self):
        with pytest.raises(KeyError, match="Reference"):
            iou(box(0, 0, 10, 10), {"left": 0, "top": 0, "width": 10})


class TestArea:
    def test_area_of_a_normal_box(self):
        assert area(box(5, 5, 4, 3)) == pytest.approx(12.0)

    def test_negative_extents_have_no_area(self):
        assert area(box(0, 0, -4, 3)) == 0.0


class TestCentreDistance:
    def test_concentric_boxes_are_zero_apart(self):
        assert centre_distance(box(0, 0, 10, 10), box(2, 2, 6, 6)) == pytest.approx(0.0)

    def test_distance_is_euclidean(self):
        # Centres at (5,5) and (8,9): dx=3, dy=4 -> 5.
        assert centre_distance(box(0, 0, 10, 10), box(3, 4, 10, 10)) == pytest.approx(5.0)

    def test_separates_near_miss_from_wrong_line(self):
        # Both score IoU 0, but one is adjacent and one is far away. This is
        # why distance is reported next to IoU rather than instead of it.
        reference = box(0, 0, 10, 10)
        adjacent = box(11, 0, 10, 10)
        far = box(500, 500, 10, 10)
        assert iou(adjacent, reference) == 0.0
        assert iou(far, reference) == 0.0
        assert centre_distance(adjacent, reference) < centre_distance(far, reference)
