import logging
import os
import time

import google.generativeai as genai
from dotenv import load_dotenv
from google.api_core import exceptions as google_exceptions
from PIL import Image

logger = logging.getLogger(__name__)

load_dotenv()

MODEL_NAME = "gemini-3.1-flash-lite"

# Every network call is bounded. Without these a lost connection left the UI
# on "Asking Gemini..." forever, because there was no timeout anywhere.
REQUEST_TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 3
INITIAL_BACKOFF_SECONDS = 1.0

# Transient server-side conditions worth a retry. Anything else (bad key,
# malformed request, safety block) fails immediately -- retrying would just
# make the user wait longer for the same answer.
RETRYABLE_ERRORS = (
    google_exceptions.ServiceUnavailable,
    google_exceptions.TooManyRequests,
    google_exceptions.DeadlineExceeded,
    google_exceptions.InternalServerError,
)


class TutorConfigError(RuntimeError):
    """Raised when the Gemini client cannot be configured."""


class ModelResponseError(RuntimeError):
    """Raised when the model returns nothing usable."""


_model = None


def get_model():
    """Returns the shared Gemini model, configuring the client on first use.

    Built lazily so that importing this module does not perform network
    configuration as a side effect, and so a missing key fails here with a
    clear message instead of surfacing as an opaque error at call time.

    Raises:
        TutorConfigError: If GEMINI_API_KEY is not set.
    """
    global _model
    if _model is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise TutorConfigError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add "
                "your key from https://aistudio.google.com/apikey"
            )
        genai.configure(api_key=api_key)
        _model = genai.GenerativeModel(MODEL_NAME)
    return _model


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
        google.api_core.exceptions.GoogleAPIError: If every attempt failed.
    """
    model = get_model()
    backoff = INITIAL_BACKOFF_SECONDS
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return model.generate_content(
                parts, request_options={"timeout": timeout}
            )
        except RETRYABLE_ERRORS as exc:
            last_error = exc
            if attempt == max_attempts:
                break
            logger.warning(
                "Gemini call failed (attempt %s/%s): %s. Retrying in %.1fs.",
                attempt, max_attempts, exc, backoff,
            )
            time.sleep(backoff)
            backoff *= 2

    logger.error("Gemini call failed after %s attempts.", max_attempts)
    raise last_error


def response_text(response):
    """Extracts the text of a response, failing loudly when there is none.

    ``response.text`` raises when the candidate list is empty or the answer was
    safety-blocked. Left unhandled that surfaced to the user as a generic
    "Error: ..." string indistinguishable from a network fault.

    Raises:
        ModelResponseError: If the response carries no usable text.
    """
    block_reason = getattr(
        getattr(response, "prompt_feedback", None), "block_reason", None
    )
    if block_reason:
        raise ModelResponseError(
            f"The request was blocked by a safety filter ({block_reason})."
        )

    try:
        text = response.text
    except Exception as exc:
        raise ModelResponseError(
            f"The model returned no usable content ({exc})."
        ) from exc

    if not text or not text.strip():
        raise ModelResponseError("The model returned an empty response.")
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
        """
    }

    if mode not in prompts:
        logger.warning("Unknown tutor mode %r; using 'student'.", mode)
    prompt = prompts.get(mode, prompts["student"])

    response = generate_content([prompt, image])

    return response_text(response)
