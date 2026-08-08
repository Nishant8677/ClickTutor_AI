"""Compares Tesseract against Florence-2 on screens Tesseract reads badly.

The locator experiments established that OCR wins on readable screens and a
vision model wins on unreadable ones. That framing assumes OCR quality is
fixed. It is not -- it is one component, and swapping it changes which regime
a screen falls into.

This measures the swap. The question is not "which engine is better" in the
abstract, but whether either produces text close enough to what is on screen
that an anchor grounded on it means something.

Ground truth is the set of fragments the vision model named and that were
hand-verified as correctly located in benchmarks/hostile_locator.json. Those
are phrases known to be visible. For each, both engines are scored on how
close their best line comes to it.

  similarity >= 0.8   close enough that an anchor quoting it would resolve
                      against the same engine's output, which is what
                      grounding requires
  similarity  < 0.5   the engine did not read that text in any useful sense

    python tools/ocr_engine_comparison.py

Writes benchmarks/ocr_engine_comparison.json. Needs FAL_KEY.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from src.console import configure_stdio  # noqa: E402
from src.florence_ocr import FlorenceError, read_regions  # noqa: E402
from src.ocr_locator import (  # noqa: E402
    build_words,
    extract_ocr_data,
    get_line_texts,
    normalize,
    similarity,
)
from tools.benchmark import OUTPUT_DIR, environment  # noqa: E402

logger = logging.getLogger(__name__)

CORPUS_DIR = Path("benchmarks/hostile")
GROUND_TRUTH = OUTPUT_DIR / "hostile_locator.json"

# Grounding copies anchors character-for-character out of OCR output, so an
# engine only has to be self-consistent to be usable -- but a phrase this far
# from the truth is no longer the phrase a reader sees on screen.
USABLE_SIMILARITY = 0.8
FAILED_SIMILARITY = 0.5


def best_match(fragment: str, lines: list[str]) -> float:
    """Returns how closely the best of these lines matches the fragment."""
    target = normalize(fragment)
    if not target or not lines:
        return 0.0
    return max(similarity(normalize(line), target) for line in lines)


def verified_fragments() -> dict[str, list[str]]:
    """Phrases known to be on screen, keyed by image.

    Taken from the hand-scored hostile run: a fragment counts only where the
    box drawn for it was judged correct by eye, which is what makes it
    evidence that the text is really there.
    """
    saved = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    truth: dict[str, list[str]] = {}
    for record in saved["per_image"]:
        # These results were written on Windows, so the stored paths use
        # backslashes. On POSIX that is an ordinary character, not a
        # separator, and Path(...).stem silently returns the whole string --
        # which reads as "no fragments for this image" rather than as an error.
        stem = Path(record["image"].replace("\\", "/")).stem
        truth[stem] = [
            entry["fragment"] for entry in record["located"] if entry.get("verdict") == "correct"
        ]
    return truth


def compare(path: Path, fragments: list[str]) -> dict:
    """Scores both engines against the known-visible phrases for one image."""
    ocr_data = extract_ocr_data(str(path))
    words = build_words(ocr_data)
    tesseract_lines = [text.strip() for text in get_line_texts(words).values() if text.strip()]
    tesseract_confidence = mean(w["confidence"] for w in words) if words else None

    image = Image.open(path)
    started = time.perf_counter()
    regions = read_regions(image)
    florence_ms = (time.perf_counter() - started) * 1000
    florence_lines = [region.text for region in regions]

    per_fragment = []
    for fragment in fragments:
        per_fragment.append(
            {
                "fragment": fragment,
                "tesseract": best_match(fragment, tesseract_lines),
                "florence": best_match(fragment, florence_lines),
            }
        )

    return {
        "image": str(path),
        "tesseract_mean_confidence": tesseract_confidence,
        "tesseract_lines": len(tesseract_lines),
        "florence_regions": len(regions),
        "florence_ms": florence_ms,
        "fragments": per_fragment,
        "tesseract_sample": tesseract_lines[:3],
        "florence_sample": florence_lines[:3],
    }


def summarise(records: list[dict]) -> dict:
    scores = [f for record in records for f in record["fragments"]]
    if not scores:
        return {}

    def block(engine: str) -> dict:
        values = [s[engine] for s in scores]
        return {
            "mean_similarity": mean(values),
            "median_similarity": median(values),
            "usable": sum(1 for v in values if v >= USABLE_SIMILARITY),
            "failed": sum(1 for v in values if v < FAILED_SIMILARITY),
        }

    return {
        "fragments_scored": len(scores),
        "tesseract": block("tesseract"),
        "florence": block("florence"),
        "florence_ms": mean(r["florence_ms"] for r in records),
    }


def main() -> int:
    configure_stdio()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    truth = verified_fragments()
    records = []
    for path in sorted(CORPUS_DIR.glob("*.png")):
        fragments = truth.get(path.stem, [])
        if not fragments:
            logger.warning("No verified fragments for %s; skipping", path.name)
            continue
        try:
            records.append(compare(path, fragments))
        except FlorenceError as exc:
            logger.error("Florence failed on %s: %s", path.name, exc)
            continue
        logger.info("%s: %s fragments scored", path.name, len(fragments))

    if not records:
        logger.error("Nothing compared; not writing results.")
        return 1

    results = {
        "environment": environment(),
        "engine": "fal-ai/florence-2-large/ocr-with-region",
        "method": (
            "Ground truth is the hand-verified fragments from "
            "hostile_locator.json. Each engine is scored on how closely its "
            "best line matches, so this measures reading quality rather than "
            "anchor resolution."
        ),
        "usable_similarity": USABLE_SIMILARITY,
        "summary": summarise(records),
        "per_image": records,
    }

    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / "ocr_engine_comparison.json"
    out.write_text(json.dumps(results, indent=4), encoding="utf-8")
    logger.info("Results -> %s", out)

    s = results["summary"]
    print(f"\n{'engine':12} {'mean sim':>9} {'median':>8} {'usable >=0.8':>13} {'failed <0.5':>12}")
    print("-" * 60)
    for engine in ("tesseract", "florence"):
        b = s[engine]
        print(
            f"{engine:12} {b['mean_similarity']:>9.3f} {b['median_similarity']:>8.3f} "
            f"{b['usable']:>8}/{s['fragments_scored']:<4} {b['failed']:>7}/{s['fragments_scored']}"
        )
    print(f"\nFlorence call: {s['florence_ms']:.0f}ms mean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
