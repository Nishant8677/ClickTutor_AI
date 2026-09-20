"""One in-memory study session: the accepted questions and answers so far.

Personal tutor roadmap, stage 4. A session starts empty with the app and is
replaced, never persisted, when the learner asks for a new one. Only accepted
lessons are recorded -- a cancelled, failed, stale or empty result adds
nothing -- and what is kept is bounded here, locally, so a long evening of
studying cannot grow the model's context without limit.

Pure Python on purpose: no Qt, no tokenizer, no I/O. The controller owns the
session and hands each request an immutable snapshot of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Complete user/assistant exchanges kept, stored oldest first; eviction
# removes from the oldest end, so the newest survive.
MAX_EXCHANGES = 8
# Characters of question and answer text kept across the whole history. A
# character budget rather than a token count: it needs no tokenizer, and the
# prompt already carries a screenshot, so the exact token figure is dominated
# by the image anyway.
MAX_HISTORY_CHARS = 12_000

USER_ROLE = "user"
ASSISTANT_ROLE = "assistant"


@dataclass(frozen=True)
class Exchange:
    """One accepted question and the answer the model gave it."""

    question: str
    answer: str

    def chars(self) -> int:
        return len(self.question) + len(self.answer)

    def messages(self) -> list[dict[str, str]]:
        """The exchange in the role/content shape LessonEngine reads. Fresh dicts."""
        return [
            {"role": USER_ROLE, "content": self.question},
            {"role": ASSISTANT_ROLE, "content": self.answer},
        ]


def bound_exchanges(
    exchanges: Sequence[Exchange],
    max_exchanges: int = MAX_EXCHANGES,
    max_chars: int = MAX_HISTORY_CHARS,
) -> tuple[Exchange, ...]:
    """Trims a history to its newest exchanges within both budgets.

    Oldest complete exchanges go first. Only when the single newest exchange
    is by itself over the character budget is anything cut inside an
    exchange: its question is kept whole and its answer clipped to whatever
    room is left, so the pair stays a pair and never exceeds the budget. A
    question that alone exceeds the budget is clipped to it, and its answer
    then has no room -- unrealistic from a one-line field, but bounded.

    Args:
        exchanges: Oldest first.
        max_exchanges: Most exchanges to keep; non-positive keeps none.
        max_chars: Most characters of question and answer text to keep.

    Returns:
        The kept exchanges, oldest first, as a new tuple.
    """
    if max_exchanges <= 0 or max_chars <= 0:
        return ()
    kept = list(exchanges[-max_exchanges:])
    while len(kept) > 1 and sum(e.chars() for e in kept) > max_chars:
        kept.pop(0)
    if kept and kept[0].chars() > max_chars:
        kept[0] = _fit_exchange(kept[0], max_chars)
    return tuple(kept)


def _fit_exchange(exchange: Exchange, budget: int) -> Exchange:
    question = exchange.question[:budget]
    return Exchange(question, exchange.answer[: budget - len(question)])


class StudySession:
    """The accepted exchanges of one app run, bounded and immutable from outside.

    Args:
        session_id: Identity a request carries so that a result from a
            session the learner has since ended is recognised and dropped.
        max_exchanges: See :func:`bound_exchanges`.
        max_chars: See :func:`bound_exchanges`.
    """

    def __init__(
        self,
        session_id: int,
        max_exchanges: int = MAX_EXCHANGES,
        max_chars: int = MAX_HISTORY_CHARS,
    ) -> None:
        self._session_id = session_id
        self._max_exchanges = max_exchanges
        self._max_chars = max_chars
        self._exchanges: tuple[Exchange, ...] = ()

    @property
    def session_id(self) -> int:
        return self._session_id

    @property
    def exchanges(self) -> tuple[Exchange, ...]:
        """Kept exchanges, oldest first. A tuple of frozen values: not mutable."""
        return self._exchanges

    @property
    def is_empty(self) -> bool:
        return not self._exchanges

    def snapshot(self) -> tuple[Exchange, ...]:
        """What a request made now should carry as its prior context."""
        return self._exchanges

    def record(self, question: str, answer: str) -> None:
        """Appends an accepted exchange and re-applies the budgets.

        A blank question records nothing: there would be no user turn for
        the answer to belong to.
        """
        question = (question or "").strip()
        if not question:
            return
        exchange = Exchange(question, (answer or "").strip())
        self._exchanges = bound_exchanges(
            (*self._exchanges, exchange), self._max_exchanges, self._max_chars
        )
