"""Tests the phrase-level routing rule on screens it was not derived from.

The rule shipped in d192a5f: trust an OCR box when the whole phrase matched,
treat a single-word match as a miss. It came from 52 matches across 16 images
that were chosen -- some deliberately to be hard -- so it is fitted to them.
A rule with perfect separation on its own training set says little.

This runs the shipping pipeline over screenshots taken after the rule was
committed, records which pass located each anchor, and draws the box so the
outcome can be judged by eye. The cross-tabulation of pass against verdict is
the test: if word-level matches are wrong here too, the rule generalises.

Verdicts are added by hand. There is no way around that -- deciding whether a
box is on the right words is the thing being measured, so no automatic scorer
can supply it without assuming the answer.

    python tools/router_validation.py
    python tools/router_validation.py --redraw

Writes benchmarks/router_validation.json and overlays beside the corpus.
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

from src.console import configure_stdio  # noqa: E402
from src.lesson_engine import LessonEngine  # noqa: E402
from src.ocr_locator import (  # noqa: E402
    TRUSTED_PASSES,
    build_words,
    extract_ocr_data,
    find_text_detailed,
)
from tools.benchmark import OUTPUT_DIR, QUESTION, RATE_LIMIT_SLEEP_S, environment  # noqa: E402

logger = logging.getLogger(__name__)

CORPUS_DIR = Path("benchmarks/heldout")
OVERLAY_DIR = CORPUS_DIR / "overlays"
_COLOURS = ("#e74c3c", "#2ecc71", "#3498db", "#f1c40f", "#9b59b6", "#e67e22")


def draw_overlay(image: Image.Image, boxes: list[tuple[int, dict]], out: Path) -> None:
    """Draws each located box, numbered, so a human can score them."""
    canvas = image.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)
    width = max(2, round(min(canvas.width, canvas.height) / 400))

    for index, box in boxes:
        colour = _COLOURS[(index - 1) % len(_COLOURS)]
        draw.rectangle(
            [box["left"], box["top"], box["left"] + box["width"], box["top"] + box["height"]],
            outline=colour,
            width=width,
        )
        draw.text((box["left"] + 2, max(0, box["top"] - 13)), str(index), fill=colour)

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)


def examine(path: Path) -> dict | None:
    """Generates a lesson and records how each anchor was located."""
    ocr_data = extract_ocr_data(str(path))
    words = build_words(ocr_data)
    if not words:
        logger.warning("Skipping %s: OCR produced no words", path.name)
        return None

    # vision_locator left off deliberately: this measures the OCR pass rule,
    # and a fallback that quietly rescued a bad match would hide the thing
    # under test.
    engine = LessonEngine(str(path), ocr_data)
    try:
        _, _, steps = engine.generate_lesson(QUESTION, [], "")
    except Exception as exc:
        logger.error("Lesson failed for %s: %s", path.name, exc)
        return None

    anchors, drawn = [], []
    for step in steps:
        phrase = step.get("anchor", "").strip()
        if not phrase or phrase.upper() == "NONE":
            continue

        box, which = find_text_detailed(ocr_data, phrase, step.get("context"))
        record = {
            "phrase": phrase,
            "pass": which,
            "trusted": which in TRUSTED_PASSES,
            "box": box,
            # Filled in by hand from the overlay.
            "verdict": None,
        }
        anchors.append(record)
        if box:
            drawn.append((len(anchors), box))

    overlay = OVERLAY_DIR / f"{path.stem}_boxes.png"
    draw_overlay(Image.open(path), drawn, overlay)

    return {
        "image": str(path).replace("\\", "/"),
        "ocr_words": len(words),
        "ocr_mean_confidence": mean(w["confidence"] for w in words),
        "repair_calls": engine.last_repair_attempts,
        "anchors": anchors,
        "overlay": str(overlay).replace("\\", "/"),
    }


def crosstab(records: list[dict]) -> dict:
    """Counts verdicts by pass, which is the whole test."""
    by_pass: dict[str, dict[str, int]] = {}
    for record in records:
        for anchor in record["anchors"]:
            name = str(anchor["pass"])
            bucket = by_pass.setdefault(name, {"correct": 0, "wrong": 0, "unscored": 0})
            verdict = anchor.get("verdict")
            bucket[verdict if verdict in ("correct", "wrong") else "unscored"] += 1

    trusted = {"correct": 0, "wrong": 0}
    untrusted = {"correct": 0, "wrong": 0}
    for name, bucket in by_pass.items():
        target = trusted if name in TRUSTED_PASSES else untrusted
        target["correct"] += bucket["correct"]
        target["wrong"] += bucket["wrong"]

    return {"by_pass": by_pass, "trusted": trusted, "word_level": untrusted}


def main() -> int:
    configure_stdio()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--redraw", action="store_true", help="redraw overlays, no API calls")
    args = parser.parse_args()

    out = OUTPUT_DIR / "router_validation.json"

    if args.redraw:
        saved = json.loads(out.read_text(encoding="utf-8"))
        for record in saved["per_image"]:
            boxes = [(i, a["box"]) for i, a in enumerate(record["anchors"], 1) if a.get("box")]
            path = Path(record["image"])
            draw_overlay(Image.open(path), boxes, OVERLAY_DIR / f"{path.stem}_boxes.png")
        logger.info("Redrew %s overlays", len(saved["per_image"]))
        return 0

    records = []
    for index, path in enumerate(sorted(CORPUS_DIR.glob("*.png"))):
        logger.info("Examining %s", path.name)
        record = examine(path)
        if record:
            records.append(record)
        if index:
            time.sleep(RATE_LIMIT_SLEEP_S)

    if not records:
        logger.error("Nothing examined; not writing results.")
        return 1

    results = {
        "environment": environment(),
        "method": (
            "Screenshots taken after the routing rule was committed. Each "
            "anchor records which locator pass matched it; verdicts are added "
            "by hand from the overlays. Vision fallback is disabled so the "
            "rule is measured on its own."
        ),
        "crosstab": crosstab(records),
        "per_image": records,
    }
    OUTPUT_DIR.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=4), encoding="utf-8")
    logger.info("Results -> %s", out)

    total = sum(len(r["anchors"]) for r in records)
    print(f"\n{len(records)} images, {total} anchors\n")
    print(f"{'pass':16} {'anchors':>8}  trusted?")
    print("-" * 40)
    for name, bucket in sorted(results["crosstab"]["by_pass"].items()):
        n = sum(bucket.values())
        print(f"{name:16} {n:>8}  {'yes' if name in TRUSTED_PASSES else 'NO'}")
    print(f"\nOverlays in {OVERLAY_DIR} -- score them by eye, then fill in verdicts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
