"""The Nemotron adapter's box conversion, which needs no model to check."""

from src.nemotron_ocr import regions_from_predictions


def test_normalised_box_becomes_pixels_on_the_original_image():
    regions = regions_from_predictions(
        [
            {
                "text": "Save",
                "confidence": 0.9,
                "left": 0.1,
                "right": 0.3,
                "upper": 0.5,
                "lower": 0.4,
            }
        ],
        width=1000,
        height=500,
    )
    assert [r.text for r in regions] == ["Save"]
    assert regions[0].box == {"left": 100, "top": 200, "width": 200, "height": 50}


def test_edges_are_taken_by_value_not_by_name():
    # The model names the larger y "upper". Swapping the values must give the
    # same box, so the conversion cannot be trusting the names.
    swapped = regions_from_predictions(
        [{"text": "x", "left": 0.3, "right": 0.1, "upper": 0.4, "lower": 0.5}], 1000, 500
    )
    assert swapped[0].box == {"left": 100, "top": 200, "width": 200, "height": 50}


def test_empty_and_malformed_regions_are_skipped_without_losing_the_rest():
    regions = regions_from_predictions(
        [
            {"text": "", "left": 0, "right": 1, "upper": 1, "lower": 0},
            {"text": "bad", "left": "x"},
            {"text": "ok", "left": 0.0, "right": 0.0, "upper": 0.0, "lower": 0.0},
        ],
        200,
        100,
    )
    assert [r.text for r in regions] == ["ok"]
    assert regions[0].box["width"] == 1 and regions[0].box["height"] == 1
