"""Tests vision localisation on screens OCR cannot read.

The main locator experiment scored both locators automatically by reading the
OCR words inside a returned box. That method cannot work here: these are the
images where OCR output is garbage, so it has no standing to judge anything.

So this tool measures what it can automatically and renders the rest for a
human. For each image it:

  * records OCR's mean confidence and what the shipping locator does
  * asks the model, from the image alone, to name a few visible fragments
  * asks it to locate each one
  * draws the boxes onto a copy for visual scoring

Verdicts are added by hand afterwards. That is slower and less tidy than a
computed score, and it is the only honest option when the automatic reader is
the component under test.

    python tools/hostile_locator_experiment.py
    python tools/hostile_locator_experiment.py --model gemini-3.1-pro

Writes benchmarks/hostile_locator.json and overlay PNGs beside the corpus.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from src import tutor  # noqa: E402
from src.console import configure_stdio  # noqa: E402
from src.ocr_locator import build_words, extract_ocr_data, find_text  # noqa: E402
from src.tutor import generate_content, response_text  # noqa: E402
from src.vision_locator import locate_phrase  # noqa: E402
from tools.benchmark import OUTPUT_DIR, RATE_LIMIT_SLEEP_S, environment  # noqa: E402

logger = logging.getLogger(__name__)

CORPUS_DIR = Path("benchmarks/hostile")
OVERLAY_DIR = CORPUS_DIR / "overlays"
FRAGMENTS_PER_IMAGE = 3

_FRAGMENT_PROMPT = (
    "Look at this image and name the {n} most prominent pieces of text or "
    "labelled elements you can see, exactly as they appear.\n"
    "Reply with JSON and nothing else: "
    '{{"fragments": ["...", "...", "..."]}}\n'
    "Keep each fragment short -- a few words at most. If the image contains "
    'no legible text, reply {{"fragments": []}}.'
)

_BOX_COLOURS = ("#e74c3c", "#2ecc71", "#3498db", "#f1c40f", "#9b59b6")


def read_fragments(image: Image.Image, count: int) -> list[str]:
    """Asks the model what text it can see, using no OCR at all."""
    raw = response_text(generate_content([_FRAGMENT_PROMPT.format(n=count), image]))
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Fragment listing was not JSON: %r", raw[:150])
        return []

    fragments = payload.get("fragments") if isinstance(payload, dict) else None
    if not isinstance(fragments, list):
        return []
    return [str(f).strip() for f in fragments if str(f).strip()][:count]


def draw_overlay(image: Image.Image, boxes: list[tuple[str, dict | None]], out: Path) -> None:
    """Renders the located boxes onto a copy of the image for visual scoring."""
    canvas = image.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)
    # Scale line width with image size so a box stays visible on a 2000px
    # screenshot and does not swamp a 168px one.
    width = max(2, round(min(canvas.width, canvas.height) / 250))

    for index, (label, box) in enumerate(boxes):
        if not box:
            continue
        colour = _BOX_COLOURS[index % len(_BOX_COLOURS)]
        draw.rectangle(
            [box["left"], box["top"], box["left"] + box["width"], box["top"] + box["height"]],
            outline=colour,
            width=width,
        )
        draw.text((box["left"] + 2, max(0, box["top"] - 12)), f"{index + 1}", fill=colour)
        logger.debug("box %s for %r", index + 1, label)

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)


def examine(path: Path) -> dict:
    """Runs the vision-only path over one image and records everything."""
    ocr_data = extract_ocr_data(str(path))
    words = build_words(ocr_data)
    confidence = mean(w["confidence"] for w in words) if words else None

    image = Image.open(path)
    fragments = read_fragments(image, FRAGMENTS_PER_IMAGE)
    time.sleep(RATE_LIMIT_SLEEP_S)

    located: list[dict] = []
    drawn: list[tuple[str, dict | None]] = []
    for fragment in fragments:
        try:
            result = locate_phrase(image, fragment)
        except Exception as exc:
            logger.error("Vision locator failed for %r: %s", fragment, exc)
            located.append({"fragment": fragment, "error": str(exc)})
            continue
        time.sleep(RATE_LIMIT_SLEEP_S)

        # What the shipping locator would have done with the same phrase. On
        # these images it is expected to fail; recorded so that is evidenced
        # rather than assumed.
        ocr_box = find_text(ocr_data, fragment, None)
        located.append(
            {
                "fragment": fragment,
                "vision_box": result.box,
                "ocr_box": ocr_box,
                "ocr_would_locate": ocr_box is not None,
                # Filled in by hand after looking at the overlay.
                "verdict": None,
            }
        )
        drawn.append((fragment, result.box))

    overlay = OVERLAY_DIR / f"{path.stem}_boxes.png"
    draw_overlay(image, drawn, overlay)

    return {
        "image": str(path),
        "image_size": [image.width, image.height],
        "ocr_words": len(words),
        "ocr_mean_confidence": confidence,
        "fragments_named": len(fragments),
        "located": located,
        "overlay": str(overlay),
    }


def main() -> int:
    configure_stdio()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="override the model, e.g. gemini-3.1-pro")
    parser.add_argument("--out", default="hostile_locator.json", help="results filename")
    parser.add_argument(
        "--redraw",
        action="store_true",
        help="redraw overlays from the saved results, without calling the API",
    )
    args = parser.parse_args()

    if args.redraw:
        # Overlays are derived from boxes that are already committed, so they
        # are regenerated rather than stored -- they were half the corpus size.
        saved = json.loads((OUTPUT_DIR / args.out).read_text(encoding="utf-8"))
        for record in saved["per_image"]:
            path = Path(record["image"])
            drawn = [(e["fragment"], e.get("vision_box")) for e in record["located"]]
            draw_overlay(Image.open(path), drawn, OVERLAY_DIR / f"{path.stem}_boxes.png")
        logger.info("Redrew %s overlays in %s", len(saved["per_image"]), OVERLAY_DIR)
        return 0

    if args.model:
        # Module-level constant, so both the fragment call and the locator
        # pick it up. Recorded in the results either way.
        tutor.MODEL_NAME = args.model
        logger.info("Model overridden to %s", args.model)

    images = sorted(CORPUS_DIR.glob("*.png"))
    if not images:
        logger.error("No images in %s", CORPUS_DIR)
        return 1

    records = []
    for path in images:
        logger.info("Examining %s", path.name)
        records.append(examine(path))

    results = {
        "environment": {**environment(), "model": tutor.MODEL_NAME},
        "method": (
            "Fragments are named by the model from the image alone, with no "
            "OCR input, then localised by the same model. OCR cannot score "
            "these images, so verdicts are added by hand from the overlays."
        ),
        "per_image": records,
    }

    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / args.out
    out.write_text(json.dumps(results, indent=4), encoding="utf-8")
    logger.info("Results -> %s", out)

    print(f"\n{'image':34} {'conf':>6} {'frags':>6} {'boxed':>6}  {'OCR would locate':>16}")
    print("-" * 84)
    for record in records:
        conf = record["ocr_mean_confidence"]
        boxed = sum(1 for entry in record["located"] if entry.get("vision_box"))
        ocr_hits = sum(1 for entry in record["located"] if entry.get("ocr_would_locate"))
        name = Path(record["image"]).stem[:33]
        print(
            f"{name:34} {conf if conf is None else round(conf, 1):>6} "
            f"{record['fragments_named']:>6} {boxed:>6}  {ocr_hits:>16}"
        )
    print(f"\nOverlays in {OVERLAY_DIR} -- score them by eye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
