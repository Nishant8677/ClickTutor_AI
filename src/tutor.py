import logging
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from PIL import Image

logger = logging.getLogger(__name__)

# google-genai logs "AFC is enabled" and an entry for every HTTP request at
# INFO, which buries the application's own output and would appear in a demo
# recording. Warnings and errors still come through.
for _noisy in ("google_genai", "google_genai.models", "httpx"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

load_dotenv()

MODEL_NAME = "gemini-3.1-flash-lite"

# Every network call is bounded. Without these a lost connection left the UI
# on "Asking Gemini..." forever, because there was no timeout anywhere.
REQUEST_TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 3
INITIAL_BACKOFF_SECONDS = 1.0

# google-genai expresses request timeouts in milliseconds, not seconds. Passing
# a seconds value straight through would set a 60ms deadline and fail every
# call, so the conversion is explicit here.
_MS_PER_SECOND = 1000

# HTTP status codes worth retrying. ServerError already covers 5xx; 429 is a
# ClientError but is transient, unlike a bad key or a malformed request, where
# retrying only makes the user wait longer for the same answer.
RETRYABLE_CLIENT_STATUSES = frozenset({429})


class TutorConfigError(RuntimeError):
    """Raised when the Gemini client cannot be configured."""


class ModelResponseError(RuntimeError):
    """Raised when the model returns nothing usable."""


_client = None


def get_client():
    """Returns the shared Gemini client, created on first use.

    Built lazily so that importing this module does not perform network
    configuration as a side effect, and so a missing key fails here with a
    clear message instead of surfacing as an opaque error at call time.

    Raises:
        TutorConfigError: If GEMINI_API_KEY is not set.
    """
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise TutorConfigError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add "
                "your key from https://aistudio.google.com/apikey"
            )
        _client = genai.Client(api_key=api_key)
    return _client


def _is_retryable(exc: Exception) -> bool:
    """Whether a failed call is worth attempting again."""
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.ClientError):
        return getattr(exc, "status_code", None) in RETRYABLE_CLIENT_STATUSES
    return False


def generate_content(parts, timeout=REQUEST_TIMEOUT_SECONDS, max_attempts=MAX_ATTEMPTS):
    """Calls Gemini with a request timeout and bounded exponential backoff.

    Args:
        parts: The prompt parts to send, e.g. ``[prompt, image]``.
        timeout: Per-attempt request timeout, in seconds.
        max_attempts: Total attempts, including the first.

    Returns:
        The raw model response.

    Raises:
        TutorConfigError: If the API key is missing.
        google.genai.errors.APIError: If every attempt failed.
    """
    client = get_client()
    config = types.GenerateContentConfig(
        http_options=types.HttpOptions(timeout=int(timeout * _MS_PER_SECOND))
    )
    backoff = INITIAL_BACKOFF_SECONDS
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(model=MODEL_NAME, contents=parts, config=config)
        except Exception as exc:
            if not _is_retryable(exc):
                raise
            last_error = exc
            if attempt == max_attempts:
                break
            logger.warning(
                "Gemini call failed (attempt %s/%s): %s. Retrying in %.1fs.",
                attempt,
                max_attempts,
                exc,
                backoff,
            )
            time.sleep(backoff)
            backoff *= 2

    logger.error("Gemini call failed after %s attempts.", max_attempts)
    if last_error is None:
        # Unreachable: the loop only exits here after a retryable failure.
        # Raising something concrete beats letting `raise None` surface as a
        # confusing TypeError if that ever stops being true.
        raise RuntimeError(f"Gemini call failed after {max_attempts} attempts.")
    raise last_error


def response_text(response):
    """Extracts the text of a response, failing loudly when there is none.

    ``response.text`` returns None or raises when the candidate list is empty
    or the answer was safety-blocked. Left unhandled that surfaced to the user
    as a generic "Error: ..." string indistinguishable from a network fault.

    Raises:
        ModelResponseError: If the response carries no usable text.
    """
    # A blocked prompt never produces candidates, so report that specifically
    # rather than letting it fall through as "empty response".
    block_reason = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
    if block_reason:
        raise ModelResponseError(f"The request was blocked by a safety filter ({block_reason}).")

    try:
        text = response.text
    except Exception as exc:
        raise ModelResponseError(f"The model returned no usable content ({exc}).") from exc

    if not text or not text.strip():
        # google-genai returns None here where the legacy SDK raised, so an
        # empty candidate list reaches this branch instead of the one above.
        finish = None
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish = getattr(candidates[0], "finish_reason", None)
        detail = f" (finish reason: {finish})" if finish else ""
        raise ModelResponseError(f"The model returned an empty response{detail}.")
    return text


def explain_image(image_path, mode="student"):

    image = Image.open(image_path)

    prompts = {
        "student": """
        You are ClickTutor.

        Explain like a friendly personal tutor.

        Rules:
        - Teach from first principles.
        - Assume the student is learning for the first time.
        - Do not skip steps.
        - If information is missing, say so.

        Format:

        1. Question Summary
        2. Concepts Required
        3. Reasoning Process
        4. Step-by-Step Explanation
        5. Final Answer
        6. Common Mistakes
        """,
        "exam": """
        You are ClickTutor.

        Explain for exam preparation.

        Rules:
        - Be concise.
        - Focus on marks-scoring approach.
        - Mention formulas and shortcuts.

        Format:

        1. What is Asked
        2. Key Formula / Concept
        3. Solution
        4. Final Answer
        5. Exam Tip
        """,
        "dsa": """
        You are ClickTutor.

        Explain like a DSA interviewer and mentor.

        Rules:
        - Identify the pattern first.
        - Explain brute force.
        - Explain optimal solution.
        - Mention edge cases.
        - Mention interview pitfalls.

        Format:

        1. Problem Summary
        2. Pattern Recognition
        3. Brute Force Approach
        4. Optimal Approach
        5. Complexity Analysis
        6. Edge Cases
        7. Interview Tips
        """,
    }

    if mode not in prompts:
        logger.warning("Unknown tutor mode %r; using 'student'.", mode)
    prompt = prompts.get(mode, prompts["student"])

    response = generate_content([prompt, image])

    return response_text(response)
