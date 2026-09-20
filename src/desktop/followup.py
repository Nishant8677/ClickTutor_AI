"""Typed follow-ups about the lesson step on screen.

Personal tutor roadmap, stage 4. A follow-up is a direct answer to a short
question about the step being displayed. It never captures the screen, never
runs OCR and never regenerates the lesson: it sends the already accepted
capture image with a prompt built from an immutable snapshot of what the
learner can see, and returns plain text.

The request is built on the GUI thread from the controller's current state
and then frozen; the worker only reads it. Every field that reaches the
prompt is clipped here to a fixed character budget, so a malformed step or a
long history cannot grow the prompt without limit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from src.desktop.session import Exchange, bound_exchanges
from src.lesson_engine import MAX_VISIBLE_CHARS, MAX_VISIBLE_LINES
from src.ocr_locator import build_words, get_line_texts
from src.tutor import generate_content, response_text

logger = logging.getLogger(__name__)

# Character budgets for what is quoted into the prompt. Deterministic and
# local: applied when the request is built, before any prompt exists.
MAX_QUESTION_CHARS = 500
MAX_TITLE_CHARS = 160
MAX_ANCHOR_CHARS = 160
MAX_CONTEXT_CHARS = 300
MAX_EXPLANATION_CHARS = 1500
MAX_NOTE_CHARS = 600
# Whole exchanges kept, newest first. Five pairs is the ten role messages
# the lesson engine formats, so a follow-up never sees more than a lesson.
MAX_HISTORY_EXCHANGES = 5


def clip(text: Any, limit: int) -> str:
    """The text as a stripped string of at most ``limit`` characters."""
    if limit <= 0:
        return ""
    return str(text if text is not None else "").strip()[:limit]


def visible_text_from_ocr(
    ocr_data: Any, max_lines: int = MAX_VISIBLE_LINES, max_chars: int = MAX_VISIBLE_CHARS
) -> str:
    """The stored OCR lines as one bounded block; "" when there are none.

    Same shape and caps as the lesson engine's visible-text block, computed
    from data already in memory. No OCR runs here.
    """
    if not ocr_data or max_lines <= 0 or max_chars <= 0:
        return ""
    lines = [text.strip() for text in get_line_texts(build_words(ocr_data)).values()]
    lines = [text for text in lines if text][:max_lines]
    kept: list[str] = []
    used = 0
    for text in lines:
        # The separator counts too, so the block returned is itself bounded.
        cost = len(text) + (1 if kept else 0)
        if used + cost > max_chars:
            break
        kept.append(text)
        used += cost
    if not kept and lines:
        # Whole lines are preferred, but a first line longer than the whole
        # budget would otherwise mean no visible text at all.
        kept.append(lines[0][:max_chars])
    return "\n".join(kept)


@dataclass(frozen=True)
class FollowUpRequest:
    """One question about one displayed step of one lesson.

    Ownership is the serial and session id, exactly as for a lesson request,
    plus the lesson id and step index the question was asked about and the
    identity of the accepted capture image. A result is only accepted while
    all of them still describe what is on screen.
    """

    serial: int
    session_id: int
    lesson_id: int
    step_index: int
    question: str
    image: Any
    visible_text: str
    capture_note: str
    step_title: str
    step_anchor: str
    step_context: str
    step_explanation: str
    lesson_question: str
    history: tuple[Exchange, ...]


def build_follow_up_request(
    *,
    serial: int,
    session_id: int,
    lesson_id: int,
    step_index: int,
    question: str,
    image: Any,
    ocr_data: Any,
    capture_note: str,
    step: Any,
    lesson_question: str,
    history: tuple[Exchange, ...],
) -> FollowUpRequest:
    """Freezes everything a follow-up may refer to, clipped to its budget.

    ``step`` is read once, here; the request holds strings, so a later change
    to the lesson's step dictionary or to the session cannot reach it.
    """
    step = step if isinstance(step, dict) else {}
    return FollowUpRequest(
        serial=serial,
        session_id=session_id,
        lesson_id=lesson_id,
        step_index=step_index,
        question=clip(question, MAX_QUESTION_CHARS),
        image=image,
        visible_text=visible_text_from_ocr(ocr_data),
        capture_note=clip(capture_note, MAX_NOTE_CHARS),
        step_title=clip(step.get("title"), MAX_TITLE_CHARS),
        step_anchor=clip(step.get("anchor"), MAX_ANCHOR_CHARS),
        step_context=clip(step.get("context"), MAX_CONTEXT_CHARS),
        step_explanation=clip(step.get("explanation"), MAX_EXPLANATION_CHARS),
        lesson_question=clip(lesson_question, MAX_QUESTION_CHARS),
        history=bound_exchanges(history, MAX_HISTORY_EXCHANGES),
    )


def build_follow_up_prompt(request: FollowUpRequest) -> str:
    """The one prompt a follow-up sends, beside the stored screenshot."""
    history_text = "".join(
        f"{message['role']}: {message['content']}\n"
        for exchange in request.history
        for message in exchange.messages()
    )
    visible_block = (
        f"VISIBLE TEXT ON SCREEN (as the screen reader extracted it):\n{request.visible_text}\n\n"
        if request.visible_text
        else "VISIBLE TEXT ON SCREEN: none could be read.\n\n"
    )
    note_block = (
        f"NOTE ABOUT THE SCREENSHOT:\n{request.capture_note}\n\n" if request.capture_note else ""
    )
    context_line = f"Context: {request.step_context}\n" if request.step_context else ""
    return (
        "You are ClickTutor, a friendly visual tutor. The student is looking at the "
        "screenshot attached, at one step of a lesson you already gave, and has a "
        "follow-up question about that step.\n\n"
        f"{note_block}"
        f"{visible_block}"
        f"ORIGINAL LESSON QUESTION:\n{request.lesson_question or '(none)'}\n\n"
        "CURRENT STEP:\n"
        f"Title: {request.step_title}\n"
        f"Anchor on screen: {request.step_anchor or 'NONE'}\n"
        f"{context_line}"
        f"Explanation given: {request.step_explanation}\n\n"
        f"CONVERSATION HISTORY:\n{history_text or '(none)'}\n"
        f"FOLLOW-UP QUESTION:\n{request.question}\n\n"
        "Answer the follow-up question directly, in a friendly tone, in about 2 to 5 "
        "sentences of plain prose. Ground the answer only in the screenshot, the "
        "current step, the visible text and the conversation history above. If "
        "the answer needs something that is not visible, say plainly what is "
        "missing instead of inventing it. Do not return lesson steps, labels or "
        "markdown headings: reply with the answer text only."
    )


def answer_follow_up(
    request: FollowUpRequest,
    generate: Callable[..., Any] = generate_content,
    extract: Callable[[Any], str] = response_text,
) -> str:
    """Makes exactly one model call for a follow-up and returns its plain text.

    Raises whatever the call or extraction raises; the worker reports it.
    """
    prompt = build_follow_up_prompt(request)
    return extract(generate([prompt, request.image])).strip()


class FollowUpWorker(QThread):
    """Answers one follow-up off the GUI thread.

    Shares the controller's single worker slot with LessonWorker, so provider
    calls never overlap. Cancellation is cooperative and honoured before the
    call starts; a call in flight drains under the engine's own caps and its
    result is then dropped by the controller.
    """

    answer_ready = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, request: FollowUpRequest) -> None:
        super().__init__()
        self.request = request

    def run(self) -> None:
        if self.isInterruptionRequested():
            logger.info("Follow-up %s cancelled before the model call.", self.request.serial)
            return
        try:
            self.answer_ready.emit(answer_follow_up(self.request))
        except Exception as exc:
            logger.exception("Follow-up %s failed", self.request.serial)
            self.error.emit(str(exc))
