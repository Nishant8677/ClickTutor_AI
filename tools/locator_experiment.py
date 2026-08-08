"""Scores vision-model localisation against the shipping OCR locator.

The question: should a highlight be positioned by naming a phrase and letting
OCR find it, or by asking the model for coordinates directly?

Method. For each corpus image a lesson is generated through the normal
pipeline. Every anchor it produces is then localised a second time by the
vision model, and that box is scored two ways.

  localised   Do the OCR words inside the vision box contain the phrase, and
              does the box hold no more than a couple of words beyond it?
              This needs no reference box, so it cannot inherit a bad one, and
              it does not care which occurrence of an ambiguous phrase the
              model chose. It is the headline number.

  best IoU    Overlap against the closest of *all* OCR occurrences of the
              phrase. Reported for tightness, and only meaningful where OCR
              placed the phrase correctly.

The first version of this tool scored IoU against the single box the shipping
locator returned. That conflated three outcomes: the vision model being wrong,
the vision model finding a different but equally valid occurrence, and the
reference box itself being wrong. All three occurred in the first run -- "arr"
appears many times in a code screenshot, and for the character "k" OCR
returned a 199-pixel box spanning most of a line. Anything averaged over that
mixture would have looked authoritative and meant nothing.

    python tools/locator_experiment.py
    python tools/locator_experiment.py --limit 3
    python tools/locator_experiment.py --dump-misses

Results are written to benchmarks/locator_comparison.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from src import tutor  # noqa: E402
from src.box_metrics import centre_distance, iou  # noqa: E402
from src.console import configure_stdio  # noqa: E402
from src.lesson_engine import LessonEngine  # noqa: E402
from src.ocr_locator import build_words, extract_ocr_data, find_text, normalize  # noqa: E402
from src.ocr_occurrences import find_all_occurrences, text_inside, words_inside  # noqa: E402
from src.vision_locator import locate_phrase  # noqa: E402
from tools.benchmark import (  # noqa: E402
    OUTPUT_DIR,
    QUESTION,
    RATE_LIMIT_SLEEP_S,
    corpus_images,
    environment,
    stats,
)

logger = logging.getLogger(__name__)

# A box counts as localised if it contains the phrase and no more than this
# many words beyond it. Without an upper bound a box covering the whole screen
# would trivially "contain" every phrase.
MAX_EXTRA_WORDS = 2

# IoU thresholds reported alongside. 0.5 is the detection-benchmark
# convention; 0.75 is included because a highlight that merely overlaps the
# right words still looks wrong on screen.
AGREEMENT_IOU = 0.5
STRICT_IOU = 0.75


def score_vision_box(ocr_data, phrase: str, vision_box) -> dict:
    """Scores one vision box without trusting any single reference box."""
    occurrences = find_all_occurrences(ocr_data, phrase)
    inside = words_inside(ocr_data, vision_box)
    phrase_present = normalize(phrase) in text_inside(ocr_data, vision_box)
    phrase_word_count = max(1, len(phrase.split()))

    best_iou, best_distance = 0.0, None
    for occurrence in occurrences:
        candidate = iou(vision_box, occurrence)
        if candidate >= best_iou:
            best_iou = candidate
            best_distance = centre_distance(vision_box, occurrence)

    return {
        "occurrences_in_ocr": len(occurrences),
        "phrase_present_in_box": phrase_present,
        "words_in_box": len(inside),
        "phrase_word_count": phrase_word_count,
        "localised": phrase_present and len(inside) <= phrase_word_count + MAX_EXTRA_WORDS,
        "best_iou": best_iou,
        "centre_distance_px": best_distance,
    }


def compare_image(path: Path) -> dict | None:
    """Runs both locators over one image and scores the vision boxes.

    Returns:
        A per-image record, or None if OCR read nothing or the lesson failed --
        neither leaves anything to compare.
    """
    ocr_data = extract_ocr_data(str(path))
    if not build_words(ocr_data):
        logger.warning("Skipping %s: OCR produced no words", path)
        return None

    engine = LessonEngine(str(path), ocr_data)
    try:
        _, _, steps = engine.generate_lesson(QUESTION, [], "")
    except Exception as exc:
        logger.error("Lesson failed for %s: %s", path, exc)
        return None

    image = Image.open(path)
    anchors: list[dict] = []

    for step in steps:
        phrase = step.get("anchor", "").strip()
        if not phrase or phrase.upper() == "NONE":
            continue

        time.sleep(RATE_LIMIT_SLEEP_S)
        started = time.perf_counter()
        try:
            located = locate_phrase(image, phrase)
        except Exception as exc:
            logger.error("Vision locator failed for %r: %s", phrase, exc)
            anchors.append({"phrase": phrase, "skipped": f"vision call failed: {exc}"})
            continue
        elapsed_ms = (time.perf_counter() - started) * 1000

        record = {
            "phrase": phrase,
            # What the shipping locator returned, for side-by-side reading.
            "ocr_box": find_text(ocr_data, phrase, step.get("context")),
            "vision_box": located.box,
            "vision_ms": elapsed_ms,
            "prompt_tokens": located.prompt_tokens,
            "response_tokens": located.response_tokens,
        }

        if located.box is None:
            # The model said "not visible", or returned something unparseable.
            # Either way no highlight was produced, which is the outcome that
            # matters, so it scores as a miss rather than being dropped.
            record.update(
                {"localised": False, "best_iou": 0.0, "not_located": True, "raw": located.raw[:200]}
            )
        else:
            record.update(score_vision_box(ocr_data, phrase, located.box))

        anchors.append(record)
        logger.info(
            "%s | %r -> localised=%s IoU %.3f",
            path.name,
            phrase[:40],
            record.get("localised"),
            record.get("best_iou", 0.0),
        )

    return {
        "image": str(path),
        "image_size": [image.width, image.height],
        "repair_calls": engine.last_repair_attempts,
        "anchors": anchors,
    }


def relocate(per_image: list[dict], model: str, sleep_s: float = RATE_LIMIT_SLEEP_S) -> list[dict]:
    """Re-runs only the vision locator over a saved run, under a different model.

    Varying the model on a full run would also change the lesson, and therefore
    the anchors, so two runs would differ in both the thing under test and the
    thing being tested on. This keeps every anchor exactly as it was and swaps
    the locator alone.

    Args:
        per_image: The ``per_image`` block of a saved run.
        model: Model id to use for the localisation calls.

    Returns:
        The same structure with vision boxes, timings and scores replaced.
    """
    tutor.MODEL_NAME = model
    for record in per_image:
        ocr_data = extract_ocr_data(record["image"])
        image = Image.open(record["image"])
        for anchor in record["anchors"]:
            phrase = anchor.get("phrase")
            if not phrase:
                continue

            time.sleep(sleep_s)
            started = time.perf_counter()
            try:
                located = locate_phrase(image, phrase)
            except Exception as exc:
                # Higher-tier models have much tighter free-tier quotas, so a
                # long run can die partway. Record the failure, keep the rest,
                # and let the summary report how many were lost rather than
                # silently averaging over a shorter list.
                logger.error("Vision locator failed for %r: %s", phrase, exc)
                anchor.clear()
                anchor.update({"phrase": phrase, "skipped": f"vision call failed: {exc}"})
                continue
            elapsed_ms = (time.perf_counter() - started) * 1000

            # Drop the previous model's scores rather than letting any survive
            # into a record that now describes a different model.
            for key in ("not_located", "raw", "localised", "best_iou", "centre_distance_px"):
                anchor.pop(key, None)

            anchor.update(
                {
                    "vision_box": located.box,
                    "vision_ms": elapsed_ms,
                    "prompt_tokens": located.prompt_tokens,
                    "response_tokens": located.response_tokens,
                }
            )
            if located.box is None:
                anchor.update(
                    {
                        "localised": False,
                        "best_iou": 0.0,
                        "not_located": True,
                        "raw": located.raw[:200],
                    }
                )
            else:
                anchor.update(score_vision_box(ocr_data, phrase, located.box))

            logger.info(
                "%s | %r -> localised=%s IoU %.3f",
                Path(record["image"]).name,
                phrase[:40],
                anchor.get("localised"),
                anchor.get("best_iou", 0.0),
            )
    return per_image


def rescore(per_image: list[dict]) -> list[dict]:
    """Re-scores a saved run, adding the OCR locator's own boxes to the ledger.

    The first full run scored only the vision boxes, which quietly compared a
    strict criterion against the accuracy benchmark's loose one: that benchmark
    asks whether a lookup returned anything, not whether the box landed on the
    phrase. OCR's box for the character "k" spanned most of a line and would
    fail the strict test too.

    OCR is deterministic and costs no API calls, so both locators can be scored
    by the same rule from a saved run without re-measuring anything.
    """
    for image in per_image:
        ocr_data = extract_ocr_data(image["image"])
        for anchor in image["anchors"]:
            if "phrase" not in anchor:
                continue
            box = anchor.get("ocr_box")
            if not box:
                anchor["ocr_localised"] = False
                continue
            scores = score_vision_box(ocr_data, anchor["phrase"], box)
            anchor["ocr_localised"] = scores["localised"]
            anchor["ocr_words_in_box"] = scores["words_in_box"]
    return per_image


def summarise(per_image: list[dict]) -> dict:
    """Aggregates per-anchor scores into the numbers worth quoting."""
    scored = [a for image in per_image for a in image["anchors"] if "localised" in a]
    skipped = [a for image in per_image for a in image["anchors"] if "skipped" in a]
    ocr_judged = [a for a in scored if "ocr_localised" in a]

    ious = [a["best_iou"] for a in scored]
    latencies = [a["vision_ms"] for a in scored if "vision_ms" in a]
    distances = [a["centre_distance_px"] for a in scored if a.get("centre_distance_px") is not None]
    localised = [a for a in scored if a["localised"]]
    ambiguous = [a for a in scored if a.get("occurrences_in_ocr", 0) > 1]

    prompt_tokens = [float(a["prompt_tokens"]) for a in scored if a.get("prompt_tokens")]
    response_tokens = [float(a["response_tokens"]) for a in scored if a.get("response_tokens")]
    repair_calls = [float(image["repair_calls"]) for image in per_image]

    return {
        "anchors_scored": len(scored),
        "anchors_skipped": len(skipped),
        "localised": len(localised),
        "localisation_rate": (len(localised) / len(scored)) if scored else None,
        # The same rule applied to the shipping locator's own boxes, so the two
        # are compared like for like rather than against different criteria.
        "ocr_localised": sum(1 for a in ocr_judged if a["ocr_localised"]),
        "ocr_localisation_rate": (
            (sum(1 for a in ocr_judged if a["ocr_localised"]) / len(ocr_judged))
            if ocr_judged
            else None
        ),
        "vision_not_located": sum(1 for a in scored if a.get("not_located")),
        "ambiguous_phrases": len(ambiguous),
        "best_iou": stats(ious),
        "iou_at_50": (sum(1 for v in ious if v >= AGREEMENT_IOU) / len(ious)) if ious else None,
        "iou_at_75": (sum(1 for v in ious if v >= STRICT_IOU) / len(ious)) if ious else None,
        "centre_distance_px": stats(distances),
        "vision_call_ms": stats(latencies),
        "vision_prompt_tokens": stats(prompt_tokens),
        "vision_response_tokens": stats(response_tokens),
        # The OCR path's cost is often quoted as two calls per lesson. This is
        # the measured figure: repair only fires when an anchor misses.
        "ocr_path_repair_calls_per_lesson": stats(repair_calls),
    }


def main() -> int:
    configure_stdio()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="only use the first N corpus images")
    parser.add_argument(
        "--dump-misses",
        action="store_true",
        help="list anchors the vision model failed to localise",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="re-score the saved run without calling the API (OCR is deterministic)",
    )
    parser.add_argument(
        "--relocate",
        metavar="MODEL",
        help="re-run only the vision locator over the saved anchors, under MODEL",
    )
    parser.add_argument(
        "--in", dest="source", default="locator_comparison.json", help="run to read"
    )
    parser.add_argument("--out", default="locator_comparison.json", help="results filename")
    parser.add_argument(
        "--sleep",
        type=float,
        default=RATE_LIMIT_SLEEP_S,
        help="seconds between calls; raise it for higher tiers, whose quotas are tighter",
    )
    parser.add_argument(
        "--api-key-env",
        metavar="VAR",
        help="environment variable holding the key to use, e.g. GEMINI_API_KEY_PRO",
    )
    args = parser.parse_args()

    if args.api_key_env:
        tutor.use_api_key_from_env(args.api_key_env)

    out = OUTPUT_DIR / args.out

    if args.relocate:
        saved = json.loads((OUTPUT_DIR / args.source).read_text(encoding="utf-8"))
        per_image = saved["per_image"]
        if args.limit:
            per_image = per_image[: args.limit]
        calls = sum(1 for r in per_image for a in r["anchors"] if a.get("phrase"))
        logger.info(
            "Re-locating %s anchors over %s images under %s, %.0fs apart (~%.0f min)",
            calls,
            len(per_image),
            args.relocate,
            args.sleep,
            calls * (args.sleep + 2) / 60,
        )
        per_image = rescore(relocate(per_image, args.relocate, args.sleep))
    elif args.rescore:
        if not out.exists():
            logger.error("No saved run at %s to re-score.", out)
            return 1
        saved = json.loads(out.read_text(encoding="utf-8"))
        per_image = rescore(saved["per_image"])
        logger.info("Re-scored %s images from the saved run; no API calls made", len(per_image))
    else:
        images = corpus_images()
        if args.limit:
            images = images[: args.limit]
        logger.info("Comparing locators over %s images", len(images))

        per_image = [record for path in images if (record := compare_image(path))]
        if not per_image:
            logger.error("No image produced a comparison; nothing written.")
            return 1
        per_image = rescore(per_image)

    results = {
        "environment": environment(),
        "method": (
            "localised = the OCR words inside the vision box contain the "
            f"phrase, with at most {MAX_EXTRA_WORDS} words beyond it. This "
            "needs no reference box. best_iou compares against the closest of "
            "all OCR occurrences of the phrase and is only meaningful where "
            "OCR placed the phrase correctly."
        ),
        "max_extra_words": MAX_EXTRA_WORDS,
        "summary": summarise(per_image),
        "per_image": per_image,
    }

    OUTPUT_DIR.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=4), encoding="utf-8")
    logger.info("Results -> %s", out)

    s = results["summary"]
    print(f"\nAnchors scored: {s['anchors_scored']}  (skipped {s['anchors_skipped']})")
    if s["localisation_rate"] is not None:
        print(
            f"LOCALISED  vision: {s['localised']}/{s['anchors_scored']} "
            f"= {s['localisation_rate'] * 100:.0f}%"
        )
    if s["ocr_localisation_rate"] is not None:
        print(
            f"           OCR:    {s['ocr_localised']}/{s['anchors_scored']} "
            f"= {s['ocr_localisation_rate'] * 100:.0f}%   (same rule, shipping locator)"
        )
    print(f"  not located:  {s['vision_not_located']}  (model reported absent or unparseable)")
    print(f"  ambiguous:    {s['ambiguous_phrases']} phrases occur more than once in OCR")
    if s["best_iou"]:
        print(f"Best IoU:       mean {s['best_iou']['avg']:.3f}, median {s['best_iou']['med']:.3f}")
        print(
            f"                {s['iou_at_50'] * 100:.0f}% at >=0.5, {s['iou_at_75'] * 100:.0f}% at >=0.75"
        )
    if s["vision_call_ms"]:
        print(f"Vision call:    {s['vision_call_ms']['avg']:.0f}ms mean")
    repair = s["ocr_path_repair_calls_per_lesson"]
    if repair:
        print(f"OCR repair calls/lesson: {repair['avg']:.2f} mean, {repair['max']:.0f} max")

    if args.dump_misses:
        print("\nNot localised -- these need a human:")
        for image in per_image:
            for anchor in image["anchors"]:
                if "localised" in anchor and not anchor["localised"]:
                    print(
                        f"  {Path(image['image']).name}: {anchor['phrase'][:50]!r} "
                        f"words_in_box={anchor.get('words_in_box')} "
                        f"present={anchor.get('phrase_present_in_box')}"
                    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
