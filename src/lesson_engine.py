import logging
import re
from pathlib import Path

from PIL import Image

from src.highlighter import highlight_box
from src.lesson_validator import (
    VALID_ATTENTIONS,
    VALID_EMPHASES,
    validate_lesson_steps,
)
from src.ocr_locator import build_words, get_line_texts, locate_trusted
from src.tutor import generate_content, response_text

logger = logging.getLogger(__name__)

# Caps on the visible-text block quoted into the prompt. A dense screenshot can
# OCR to hundreds of lines; the anchor only needs enough context to choose well,
# and the image itself still carries the full picture.
MAX_VISIBLE_LINES = 120
MAX_VISIBLE_CHARS = 4000

# Corrective calls allowed per lesson. Each costs one round trip, so this
# bounds the worst case rather than letting a poorly-read screen fan out.
MAX_ANCHOR_REPAIRS = 3

# Vision-locator calls allowed per lesson, for anchors that survive repair and
# still cannot be located as a phrase. Bounded for the same reason, and lower:
# by this point OCR has already said it cannot read the region, so a screen
# that trips it once will usually trip it repeatedly.
MAX_VISION_FALLBACKS = 2


def _repair_prompt(anchor, step, visible_text):
    """Asks for a replacement anchor, naming the phrase that was rejected."""
    return (
        f'The phrase "{anchor}" does NOT appear in the text on screen, so it '
        "cannot be highlighted.\n\nHere is every line of text actually on "
        f"screen:\n{visible_text}\n\n"
        f"The teaching point is: {step.get('explanation', '')[:220]}\n\n"
        "Reply with ONE short phrase copied character-for-character from the "
        "lines above that best anchors that teaching point. Reply with the "
        "phrase only, nothing else."
    )


STEP_PATTERN = re.compile(
    r"STEP\s+(\d+)\s*(.*?)(?=\n\s*STEP\s+\d+\s*|\Z)", re.IGNORECASE | re.DOTALL
)


def get_visible_text(response):
    match = re.search(r"ANCHOR:\s*(.+)", response, re.IGNORECASE)
    if not match:
        match = re.search(r"VISIBLE TEXT:\s*(.+)", response, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def get_context_text(response):
    match = re.search(r"CONTEXT:\s*(.+)", response, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def extract_section(text, label, next_labels=None):
    labels = next_labels or []
    next_pattern = "|".join(re.escape(item) for item in labels)

    if next_pattern:
        pattern = rf"{re.escape(label)}:\s*(.*?)(?=\n\s*(?:{next_pattern})\s*:|\Z)"
    else:
        pattern = rf"{re.escape(label)}:\s*(.*)"

    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)

    if match:
        return match.group(1).strip()

    return ""


def _coerce(value, allowed, default, field, step_number):
    """Maps a model-supplied enum field onto the vocabulary the renderer knows.

    Gemini occasionally answers with a synonym or a whole phrase. Passing that
    through means the renderer's else-branch silently draws a rectangle, so
    normalise here and say so in the log.
    """
    normalized = (value or "").strip().lower()
    if normalized in allowed:
        return normalized
    if normalized:
        logger.warning(
            "Step %s: unrecognised %s %r; falling back to %r.",
            step_number,
            field,
            value,
            default,
        )
    return default


def parse_lesson_steps(response):
    steps = []
    dropped = 0

    for match in STEP_PATTERN.finditer(response):
        step_number = int(match.group(1))
        block = match.group(2).strip()

        title = extract_section(
            block, "TITLE", ["ANCHOR", "CONTEXT", "ATTENTION", "EMPHASIS", "EXPLANATION"]
        )
        anchor = extract_section(
            block, "ANCHOR", ["CONTEXT", "ATTENTION", "EMPHASIS", "EXPLANATION"]
        )
        if not anchor:
            anchor = extract_section(
                block, "VISIBLE TEXT", ["CONTEXT", "ATTENTION", "EMPHASIS", "EXPLANATION"]
            )

        context = extract_section(block, "CONTEXT", ["ATTENTION", "EMPHASIS", "EXPLANATION"])
        attention = extract_section(block, "ATTENTION", ["EMPHASIS", "EXPLANATION"])
        emphasis = extract_section(block, "EMPHASIS", ["EXPLANATION"])
        explanation = extract_section(block, "EXPLANATION")

        if not explanation:
            # Dropping a step the model did produce is worth knowing about:
            # it usually means the prompt drifted or the label was misspelled.
            # This used to happen silently, so a half-length lesson looked
            # exactly like a correct one.
            dropped += 1
            logger.warning(
                "Dropping step %s: no EXPLANATION section found in block %r",
                step_number,
                block[:120],
            )
            continue

        steps.append(
            {
                "step": step_number,
                "title": title or f"Step {step_number}",
                "anchor": anchor or "NONE",
                "context": None if not context or context.upper() == "NONE" else context,
                "attention": _coerce(attention, VALID_ATTENTIONS, "none", "attention", step_number),
                "emphasis": _coerce(emphasis, VALID_EMPHASES, "low", "emphasis", step_number),
                "explanation": explanation,
                "highlighted_image": None,
            }
        )

    if dropped:
        logger.warning(
            "Parsed %s lesson step(s); dropped %s malformed block(s).",
            len(steps),
            dropped,
        )

    return steps


def format_lesson_answer(steps):
    if not steps:
        return ""

    parts = []

    for step in steps:
        context_str = f" | 📝 **Context:** `{step['context']}`" if step.get("context") else ""
        parts.append(
            f"### STEP {step['step']}: {step['title']}\n"
            f"🎯 **Anchor Focus:** `{step['anchor']}`{context_str} | 👁️ **Attention:** `{step['attention']}` | ⚡ **Emphasis:** `{step['emphasis']}`\n\n"
            f"{step['explanation']}"
        )

    return "\n\n".join(parts)


class LessonEngine:
    def __init__(
        self,
        image_or_path,
        ocr_data,
        mode="student",
        screenshot_type=None,
        vision_locator=None,
    ):
        self.image_or_path = image_or_path
        self.ocr_data = ocr_data
        self.mode = mode
        self.screenshot_type = screenshot_type
        # Injected so it can be turned off. Tests and the accuracy benchmark
        # need the OCR path measured on its own, and passing None keeps the
        # engine free of network calls entirely.
        self._vision_locator = vision_locator
        self._vision_fallbacks = 0
        # Corrective calls made by the most recent repair_anchors() run. Read by
        # tools/benchmark.py: the cost of the OCR path is often quoted as "two
        # API calls per lesson", but repair only fires on a miss and nothing
        # measured how often that was.
        self.last_repair_attempts = 0

    def visible_text_block(self, max_lines=MAX_VISIBLE_LINES, max_chars=MAX_VISIBLE_CHARS):
        """Renders the OCR lines for inclusion in the prompt.

        Anchors are located by searching this same OCR output, so quoting it
        back to the model makes a chosen anchor findable by construction. It
        also makes OCR errors self-consistent: if Tesseract misreads a word,
        the model copies the misreading, which still matches at lookup time.

        Returns:
            A newline-separated block, or "" when OCR produced nothing.
        """
        if not self.ocr_data:
            return ""

        lines = [text.strip() for text in get_line_texts(build_words(self.ocr_data)).values()]
        lines = [text for text in lines if text]

        kept, budget = [], max_chars
        for text in lines[:max_lines]:
            if budget - len(text) < 0:
                break
            kept.append(text)
            budget -= len(text)

        return "\n".join(kept)

    def build_lesson_prompt(self, question, history_text, explanation_text):
        type_str = ""
        type_guidelines = ""
        if self.screenshot_type:
            type_str = f"DETECTED SCREENSHOT TYPE: {self.screenshot_type}\n"
            cleaned_type = self.screenshot_type.split("(")[0].strip().lower()
            if cleaned_type == "code":
                type_guidelines = "- Because this is code, explain why this variable/structure exists, how execution flows, and common bugs with this logic."
            elif cleaned_type == "math":
                type_guidelines = "- Because this is math, guide the student through the formula, substitution, calculation, and common arithmetic errors."
            elif cleaned_type == "diagram":
                type_guidelines = "- Because this is a diagram, guide the student through overview of components, flow directions, and key relationships."
            elif cleaned_type == "dashboard":
                type_guidelines = "- Because this is a dashboard, point to key metrics, explain trend lines, and interpret the data for them."
            elif cleaned_type == "slides":
                type_guidelines = "- Because this is a presentation slide, highlight the key takeaway points and how they relate to the diagrams/illustrations on the slide."
            elif cleaned_type == "pdf":
                type_guidelines = "- Because this is a document page, focus on core definitions, formulas, and structured paragraphs of academic explanation."
            elif cleaned_type == "website":
                type_guidelines = "- Because this is a web page, direct attention to documentation blocks, headers, or relevant reading selections."

        guidelines_block = f"{type_guidelines}\n" if type_guidelines else ""

        visible_text = self.visible_text_block()
        visible_block = (
            "\nVISIBLE TEXT ON SCREEN (exactly as the screen reader extracted it).\n"
            "Every ANCHOR you return MUST be copied character-for-character from "
            "these lines. Text that is not in this list cannot be highlighted, "
            "even if you can see it in the image:\n"
            f"{visible_text}\n"
            if visible_text
            else ""
        )

        return f"""
You are ClickTutor, an AI visual tutor that teaches by guiding the student's attention step-by-step.
Your goal is to explain concepts, not just point out facts or lines. Teach WHY things are there and what they mean, rather than simply listing syntax.

You are looking at:
1. A screenshot from the student's screen.
2. Previous conversation history (if any).
3. A new student question.

{type_str}{visible_block}
ORIGINAL EXPLANATION:
{explanation_text}

CONVERSATION HISTORY:
{history_text}


CURRENT QUESTION:
{question}

Your job is to break down the answer into a short, structured lesson (3 to 6 steps).
For each step, you must focus on one specific concept and anchor it to a visible element on the screen.

CRITICAL INSTRUCTIONS FOR EXPLANATION:
{guidelines_block}- Do NOT just say "Line 5 defines X". Instead explain: WHY does X exist? What problem does X solve?
- Address consequence: What would happen or break if we removed or changed this anchored element?
- Detail common mistakes or pitfalls students make regarding this concept.
- Keep explanations clear, engaging, and friendly.

FORMAT REQUIREMENT:
For each step, return exactly these fields in order:
- TITLE: A short, conceptual title for this step (e.g. "Initializing the loop variables" or "Understanding the right-angle triangle").
- ANCHOR: Pick one word or short phrase that best anchors that teaching step. It MUST be copied character-for-character from the VISIBLE TEXT ON SCREEN list above. Do not paraphrase it, do not correct its spelling, and do not use a term you know from the topic unless that exact text appears in the list. If nothing suitable appears in the list, write NONE rather than inventing one.
- CONTEXT: If the ANCHOR text is not unique on the screen (e.g. the variable name 'count' appears multiple times, like in initialization vs. incrementing), provide the unique surrounding line of text containing the anchor to resolve duplicates (e.g. 'count++' or 'int count = 1;'). If the ANCHOR is unique, write NONE.
- ATTENTION: Specify the visual layout indicator (choose exactly one of: circle, rectangle, underline, none).
- EMPHASIS: Specify the importance level (choose exactly one of: high, medium, low).
- EXPLANATION: Your conceptual explanation for this step.

Return the steps in this exact format with nothing before STEP 1:

STEP 1
TITLE:
...
ANCHOR:
...
CONTEXT:
...
ATTENTION:
...
EMPHASIS:
...
EXPLANATION:
...

STEP 2
TITLE:
...
ANCHOR:
...
CONTEXT:
...
ATTENTION:
...
EMPHASIS:
...
EXPLANATION:
...
"""

    def _locate(self, anchor, context):
        """Finds an anchor, asking the vision locator only if OCR cannot.

        OCR is tried first because it is already computed, costs nothing more,
        and on readable screens it is both more accurate and tighter than the
        alternative. It is trusted only on a whole-phrase match: a single-word
        match was wrong every time it occurred across both benchmark corpora.

        The vision locator is the opposite trade -- a network round trip, but it
        reads handwriting, rotated labels and photographs that Tesseract cannot.
        It is asked only when OCR has declined, and at most MAX_VISION_FALLBACKS
        times per lesson.

        Returns:
            A box in image pixels, or None if neither locator could place it.
        """
        box = locate_trusted(self.ocr_data, anchor, context)
        if box or self._vision_locator is None:
            return box

        if self._vision_fallbacks >= MAX_VISION_FALLBACKS:
            logger.debug("Vision fallback budget spent; leaving %r unhighlighted", anchor)
            return None

        image = self.image_or_path
        if isinstance(image, str):
            image = Image.open(image)

        self._vision_fallbacks += 1
        try:
            located = self._vision_locator(image, anchor)
        except Exception as exc:
            # A failed fallback must not take the lesson with it: the step
            # simply renders without a highlight, as it would have anyway.
            logger.warning("Vision fallback failed for %r: %s", anchor, exc)
            return None

        if located.box:
            logger.info("Located %r by vision after OCR declined", anchor)
        return located.box

    def build_step_highlights(self, steps):
        highlighted_steps = []
        self._vision_fallbacks = 0

        for index, step in enumerate(steps, start=1):
            anchor = step.get("anchor", "")
            context = step.get("context")
            highlighted_image = None

            if anchor and anchor.strip().upper() != "NONE":
                box = self._locate(anchor, context)

                if box and isinstance(self.image_or_path, str):
                    image_path = Path(self.image_or_path)
                    highlighted_image = highlight_box(
                        self.image_or_path,
                        box,
                        image_path.with_name(f"{image_path.stem}_step_{index}_highlighted.png"),
                    )

            highlighted_step = dict(step)
            highlighted_step["highlighted_image"] = highlighted_image
            highlighted_steps.append(highlighted_step)

        return highlighted_steps

    def repair_anchors(self, steps, image, max_repairs=MAX_ANCHOR_REPAIRS):
        """Replaces anchors that cannot be located with ones that can.

        Telling the model to copy from the visible-text list is not enough on
        its own: measured over 34 steps it still returned anchors drawn from
        its knowledge of the topic rather than the screen -- "in-place" for a
        LeetCode problem whose visible text never contains the word. Those
        steps render an explanation with nothing highlighted.

        Naming the rejected phrase and asking again resolves them, and the
        replacements are better anchors than the originals ("modify the input
        2D matrix directly"). One extra call per unresolved step, bounded by
        max_repairs so a badly-matched screen cannot fan out.

        Args:
            steps: Parsed lesson steps, mutated copies are returned.
            image: The image already opened for the main call.
            max_repairs: Cap on corrective calls for one lesson.

        Returns:
            The steps, with unresolvable anchors replaced where possible.
        """
        self.last_repair_attempts = 0
        visible = self.visible_text_block()
        if not visible:
            return steps

        repaired, attempts = [], 0
        for step in steps:
            anchor = step.get("anchor", "")
            resolvable = anchor.strip().upper() == "NONE" or locate_trusted(
                self.ocr_data, anchor, step.get("context")
            )
            if resolvable or attempts >= max_repairs:
                repaired.append(step)
                continue

            attempts += 1
            try:
                replacement = (
                    response_text(generate_content([_repair_prompt(anchor, step, visible), image]))
                    .strip()
                    .strip('"')
                    .strip()
                )
            except Exception as exc:
                logger.warning("Anchor repair call failed for %r: %s", anchor, exc)
                repaired.append(step)
                continue

            if replacement and locate_trusted(self.ocr_data, replacement, None):
                logger.info("Repaired anchor %r -> %r", anchor, replacement)
                fixed = dict(step)
                fixed["anchor"] = replacement
                # The replacement is quoted from a single line, so the original
                # disambiguating context no longer applies to it.
                fixed["context"] = None
                repaired.append(fixed)
            else:
                logger.warning(
                    "Anchor repair produced %r, still not locatable; leaving step unhighlighted.",
                    replacement,
                )
                repaired.append(step)

        self.last_repair_attempts = attempts
        return repaired

    def generate_lesson(self, question, history, explanation_text):
        history_text = ""
        for item in history[-10:]:
            history_text += f"{item['role']}: {item['content']}\n"

        prompt = self.build_lesson_prompt(question, history_text, explanation_text)

        highlighted_image = None
        lesson_steps = []

        try:
            if isinstance(self.image_or_path, str):
                image = Image.open(self.image_or_path)
            else:
                image = self.image_or_path

            response = generate_content([prompt, image])

            answer = response_text(response)
            parsed_steps = parse_lesson_steps(answer)

            # The validator existed but was only ever called from the test
            # runner, so nothing checked the runtime path. Structural problems
            # are logged rather than raised: a partially valid lesson is still
            # more useful to the learner than an error dialog.
            # Anchors are repaired before highlights are built, so downstream
            # consumers only ever see anchors that resolve.
            parsed_steps = self.repair_anchors(parsed_steps, image)

            is_valid, validation_errors = validate_lesson_steps(parsed_steps)
            if not is_valid:
                logger.warning(
                    "Lesson failed validation with %s issue(s): %s",
                    len(validation_errors),
                    "; ".join(validation_errors[:5]),
                )

            lesson_steps = self.build_step_highlights(parsed_steps)

            if lesson_steps:
                highlighted_image = lesson_steps[0].get("highlighted_image")
                answer = format_lesson_answer(lesson_steps)
            else:
                anchor = get_visible_text(answer)
                context = get_context_text(answer)

                if anchor and anchor.strip().upper() != "NONE":
                    box = self._locate(anchor, context)

                    # self.image_path was never assigned by __init__, so this
                    # branch used to raise AttributeError for every input type
                    # and report it to the user as the lesson text. Guard on
                    # str the same way build_step_highlights does: highlight_box
                    # writes a sibling file, which needs a real path.
                    if box and isinstance(self.image_or_path, str):
                        source_path = Path(self.image_or_path)
                        highlighted_image = highlight_box(
                            self.image_or_path,
                            box,
                            source_path.with_name(f"{source_path.stem}_highlighted.png"),
                        )

        except Exception as e:
            logger.exception("Lesson generation failed")
            answer = f"Error: {str(e)}"
            highlighted_image = None
            lesson_steps = []

        return answer, highlighted_image, lesson_steps
