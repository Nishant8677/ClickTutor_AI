"""Measures ClickTutor's pipeline latency and anchor-localisation accuracy.

Two questions, deliberately separated because they need different inputs:

  latency   How long does a lesson take end to end? Needs a live capture, so
            it runs against whatever is on screen.
  accuracy  How often does an anchor resolve to something on screen? Needs a
            varied corpus. Measuring it against one screenshot repeated N
            times says almost nothing -- an earlier version of this file did
            exactly that and reported 100% from a single screen, while the
            same pipeline scored 79% across three different images.

Both go through generate_lesson(), so they exercise the shipping path
including anchor repair. A previous version called model.generate_content()
directly and therefore measured code that no longer represents the product.

    python tools/benchmark.py --latency-iterations 10
    python tools/benchmark.py --accuracy-only

Results and chart are written to benchmarks/. Every run records the
environment it was taken in, because a latency number without its conditions
is not reproducible.
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pytesseract  # noqa: E402

from src.capture import ScreenCapture  # noqa: E402
from src.console import configure_stdio  # noqa: E402
from src.lesson_engine import LessonEngine  # noqa: E402
from src.ocr_locator import build_words, extract_ocr_data, find_text  # noqa: E402
from src.tutor import MODEL_NAME  # noqa: E402

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("benchmarks")
QUESTION = "Can you explain the optimal approach for this problem?"

# Free-tier Gemini allows 15 requests per minute; anchor repair can add a
# second call per lesson, so leave room.
RATE_LIMIT_SLEEP_S = 4

# Images used for the accuracy corpus. Diversity is the point: repeating one
# screenshot inflates the result.
CORPUS_GLOBS = ("demo/*/screenshot.png", "tests/*/*.png")


def environment() -> dict:
    """Records the conditions a result was measured under.

    The SDK is included because latency depends on it and this project has
    already changed clients once. Without it, two sections of the same results
    file can come from different SDKs with nothing but timestamps to say so.
    """
    try:
        from PyQt6.QtCore import QT_VERSION_STR
    except Exception:
        QT_VERSION_STR = "unavailable"

    import os
    from importlib.metadata import PackageNotFoundError, version

    try:
        sdk = f"google-genai {version('google-genai')}"
    except PackageNotFoundError:
        sdk = "google-genai (version unavailable)"

    try:
        tesseract = str(pytesseract.get_tesseract_version())
    except Exception:
        tesseract = "unavailable"

    return {
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "python": platform.python_version(),
        "qt": QT_VERSION_STR,
        "qt_platform": os.environ.get("QT_QPA_PLATFORM", "default"),
        "sdk": sdk,
        "tesseract": tesseract,
        "model": MODEL_NAME,
    }


def stats(samples: list[float]) -> dict:
    if not samples:
        return {}
    return {
        "n": len(samples),
        "avg": float(np.mean(samples)),
        "med": float(np.median(samples)),
        "p95": float(np.percentile(samples, 95)),
        "max": float(np.max(samples)),
    }


def corpus_images() -> list[Path]:
    """Every candidate image, deduplicated, excluding generated highlights."""
    found: list[Path] = []
    for pattern in CORPUS_GLOBS:
        found.extend(Path().glob(pattern))
    return sorted({p for p in found if "_highlighted" not in p.name})


def measure_accuracy(images: list[Path]) -> dict:
    """Runs one lesson per image and counts anchors that resolve.

    Returns per-image detail as well as the total, so a reader can see which
    screens the pipeline handles and which it does not.
    """
    per_image, matched, total, skipped = [], 0, 0, []

    for index, image in enumerate(images):
        ocr_data = extract_ocr_data(str(image))
        if not build_words(ocr_data):
            # Tesseract read nothing, so no anchor could resolve regardless of
            # the model. Counting these would understate the pipeline rather
            # than measure it; they are reported separately.
            skipped.append(str(image))
            logger.warning("Skipping %s: OCR produced no words", image)
            continue

        try:
            _, _, steps = LessonEngine(str(image), ocr_data).generate_lesson(QUESTION, [], "")
        except Exception as exc:
            logger.error("Lesson failed for %s: %s", image, exc)
            continue

        anchored = [s for s in steps if s.get("anchor", "").strip().upper() != "NONE"]
        hits = sum(1 for s in anchored if find_text(ocr_data, s["anchor"], s.get("context")))
        matched += hits
        total += len(anchored)
        per_image.append(
            {
                "image": str(image),
                "steps": len(steps),
                "anchored_steps": len(anchored),
                "located": hits,
            }
        )
        logger.info("%s: %s/%s anchors located", image.name, hits, len(anchored))

        if index < len(images) - 1:
            time.sleep(RATE_LIMIT_SLEEP_S)

    return {
        "anchors_located": matched,
        "anchors_total": total,
        "accuracy": (matched / total) if total else None,
        "images_used": len(per_image),
        "images_skipped_no_ocr": skipped,
        "per_image": per_image,
    }


def measure_latency(iterations: int, prepare_seconds: int = 0) -> dict:
    """Times a full lesson against live screen captures.

    Whatever is on screen becomes the lesson content, so prepare_seconds
    gives the operator time to switch to something representative before the
    first capture.
    """
    capture_ms, ocr_ms, lesson_ms, lookup_ms, e2e_ms = [], [], [], [], []
    capture_engine = ScreenCapture()

    if prepare_seconds > 0:
        print(
            f"\n  Switch to the screen you want measured. First capture in {prepare_seconds}s.\n",
            flush=True,
        )
        for remaining in range(prepare_seconds, 0, -1):
            print(f"    {remaining}...", end="\r", flush=True)
            time.sleep(1)
        print("    measuring now          ", flush=True)

    for i in range(iterations):
        logger.info("Latency iteration %s/%s", i + 1, iterations)
        t0 = time.perf_counter()

        t = time.perf_counter()
        image = capture_engine.capture()
        capture_ms.append((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        ocr_data = extract_ocr_data(image)
        ocr_ms.append((time.perf_counter() - t) * 1000)

        if not build_words(ocr_data):
            logger.warning("Iteration %s: no OCR words; skipping", i + 1)
            continue

        t = time.perf_counter()
        try:
            _, _, steps = LessonEngine(image, ocr_data).generate_lesson(QUESTION, [], "")
        except Exception as exc:
            logger.error("Iteration %s failed: %s", i + 1, exc)
            continue
        lesson_ms.append((time.perf_counter() - t) * 1000)

        # Anchor lookup only. This is NOT overlay render time: no Qt, no
        # painting, no compositing happens here. A previous version labelled
        # this "render", which invited it to be read as overlay latency.
        t = time.perf_counter()
        for step in steps:
            anchor = step.get("anchor", "")
            if anchor and anchor.strip().upper() != "NONE":
                find_text(ocr_data, anchor, step.get("context"))
        lookup_ms.append((time.perf_counter() - t) * 1000)

        e2e_ms.append((time.perf_counter() - t0) * 1000)

        if i < iterations - 1:
            time.sleep(RATE_LIMIT_SLEEP_S)

    return {
        "capture_ms": stats(capture_ms),
        "ocr_ms": stats(ocr_ms),
        "lesson_ms": stats(lesson_ms),
        "anchor_lookup_ms": stats(lookup_ms),
        "end_to_end_ms": stats(e2e_ms),
    }


def write_chart(latency: dict, iterations: int) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.info("matplotlib not installed; skipping chart")
        return

    labels = ["Capture", "OCR", "Lesson (API)", "Anchor lookup", "End-to-end"]
    keys = ["capture_ms", "ocr_ms", "lesson_ms", "anchor_lookup_ms", "end_to_end_ms"]
    values = [latency.get(k, {}).get("avg", 0) for k in keys]
    if not any(values):
        return

    plt.figure(figsize=(10, 6))
    bars = plt.bar(labels, values, color=["#3498db", "#e74c3c", "#f1c40f", "#2ecc71", "#9b59b6"])
    # Title reads the real count; it used to say "30 Iterations" regardless.
    plt.title(f"Average pipeline latency ({iterations} iterations)")
    plt.ylabel("Time (ms)")
    for bar in bars:
        plt.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{int(bar.get_height())}ms",
            ha="center",
            va="bottom",
        )
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "benchmark_chart.png")
    logger.info("Chart -> %s", OUTPUT_DIR / "benchmark_chart.png")


def main() -> int:
    configure_stdio()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latency-iterations", type=int, default=10)
    parser.add_argument("--accuracy-only", action="store_true")
    parser.add_argument(
        "--prepare", type=int, default=0, help="seconds to switch screens before the first capture"
    )
    parser.add_argument("--latency-only", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / "benchmark_results.json"

    # Merge into whatever is already recorded. Latency and accuracy are
    # measured by separate runs, and an earlier version rewrote the whole file
    # each time, so running one silently discarded the other's result.
    results: dict = {}
    if out.exists():
        try:
            results = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Existing results file is unreadable; starting fresh")

    # Each section carries the environment it was measured in, since the two
    # runs can happen on different days or machines.
    env = environment()

    if not args.accuracy_only:
        results["latency"] = measure_latency(args.latency_iterations, args.prepare)
        results["latency_iterations"] = args.latency_iterations
        results["latency_environment"] = env

    if not args.latency_only:
        images = corpus_images()
        logger.info("Accuracy corpus: %s images", len(images))
        results["accuracy_detail"] = measure_accuracy(images)
        results["accuracy_environment"] = env

    out.write_text(json.dumps(results, indent=4), encoding="utf-8")
    logger.info("Results -> %s", out)

    if "latency" in results:
        write_chart(results["latency"], args.latency_iterations)

    acc = results.get("accuracy_detail")
    if acc and acc["anchors_total"]:
        print(
            f"\nANCHOR ACCURACY: {acc['anchors_located']}/{acc['anchors_total']} "
            f"= {acc['accuracy'] * 100:.0f}%  over {acc['images_used']} images"
        )
        if acc["images_skipped_no_ocr"]:
            print(f"  skipped (no OCR text): {len(acc['images_skipped_no_ocr'])}")
    if "latency" in results:
        e2e = results["latency"].get("end_to_end_ms", {})
        if e2e:
            print(f"END-TO-END: mean {e2e['avg'] / 1000:.2f}s over n={e2e['n']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
