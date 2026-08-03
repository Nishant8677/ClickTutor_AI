"""Makes stdout and stderr safe for non-ASCII text.

On Windows, Python picks the legacy ANSI codepage for redirected streams --
cp1252 here. Printing anything outside it raises UnicodeEncodeError and kills
the process. That is not hypothetical for ClickTutor: OCR reads whatever is on
the user's screen, lesson output is emoji-laden markdown, and the test runner
prints emoji status markers. Any of those reaching a redirected stdout on the
one platform the desktop app actually runs on would crash it.

Call configure_stdio() early in any entry point that prints.
"""

from __future__ import annotations

import contextlib
import sys


def configure_stdio(encoding: str = "utf-8", errors: str = "replace") -> None:
    """Reconfigures stdout and stderr to a Unicode-safe encoding.

    Uses errors="replace" rather than "strict": a diagnostic that cannot be
    encoded should degrade to a replacement character, never take down the
    program that was trying to report something.

    Args:
        encoding: Target encoding for both streams.
        errors: Codec error policy.

    Safe to call more than once, and on streams that do not support
    reconfiguration (pythonw.exe supplies None; some harnesses substitute
    objects without the method).
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # Already detached, or a stream that refuses reconfiguration. Printing
        # is best-effort from here; never fail the caller over a log stream.
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding=encoding, errors=errors)
