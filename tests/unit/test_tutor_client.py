"""Unit tests for the Gemini client boundary.

Covers the parts of the google-genai migration that are easy to get wrong and
silent when wrong: the timeout unit, and which failures are worth retrying.
No network access -- the client is faked at the boundary.
"""

import pytest
from google.genai import errors as genai_errors

import src.tutor as tutor


@pytest.fixture(autouse=True)
def reset_client(monkeypatch):
    """Each test gets a fresh lazily-built client."""
    monkeypatch.setattr(tutor, "_client", None)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, outcomes):
        self.models = FakeModels(outcomes)


def install(monkeypatch, outcomes):
    client = FakeClient(outcomes)
    monkeypatch.setattr(tutor, "get_client", lambda: client)
    monkeypatch.setattr(tutor, "INITIAL_BACKOFF_SECONDS", 0.0)
    return client


def server_error():
    return genai_errors.ServerError.__new__(genai_errors.ServerError)


def client_error(status):
    exc = genai_errors.ClientError.__new__(genai_errors.ClientError)
    exc.status_code = status
    return exc


class TestConfiguration:
    def test_missing_key_fails_with_actionable_message(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        with pytest.raises(tutor.TutorConfigError, match="GEMINI_API_KEY"):
            tutor.get_client()

    def test_client_is_built_once(self, monkeypatch):
        built = []
        monkeypatch.setattr(tutor.genai, "Client", lambda **kw: built.append(kw) or object())

        tutor.get_client()
        tutor.get_client()

        assert len(built) == 1


class TestTimeoutUnit:
    def test_timeout_is_sent_in_milliseconds(self, monkeypatch):
        # google-genai takes milliseconds. Passing seconds straight through
        # would set a 60ms deadline and fail every call.
        client = install(monkeypatch, [object()])

        tutor.generate_content(["hi"], timeout=60)

        assert client.models.calls[0]["config"].http_options.timeout == 60_000

    def test_model_name_is_passed_per_call(self, monkeypatch):
        client = install(monkeypatch, [object()])

        tutor.generate_content(["hi"])

        assert client.models.calls[0]["model"] == tutor.MODEL_NAME


class TestRetryPolicy:
    def test_server_error_is_retried_then_succeeds(self, monkeypatch):
        sentinel = object()
        client = install(monkeypatch, [server_error(), sentinel])

        assert tutor.generate_content(["hi"]) is sentinel
        assert len(client.models.calls) == 2

    def test_rate_limit_is_retried(self, monkeypatch):
        sentinel = object()
        client = install(monkeypatch, [client_error(429), sentinel])

        assert tutor.generate_content(["hi"]) is sentinel
        assert len(client.models.calls) == 2

    def test_bad_request_is_not_retried(self, monkeypatch):
        # Retrying a 400 only makes the user wait for the same answer.
        client = install(monkeypatch, [client_error(400)])

        with pytest.raises(genai_errors.ClientError):
            tutor.generate_content(["hi"])
        assert len(client.models.calls) == 1

    def test_auth_failure_is_not_retried(self, monkeypatch):
        client = install(monkeypatch, [client_error(403)])

        with pytest.raises(genai_errors.ClientError):
            tutor.generate_content(["hi"])
        assert len(client.models.calls) == 1

    def test_attempts_are_bounded(self, monkeypatch):
        client = install(monkeypatch, [server_error() for _ in range(5)])

        with pytest.raises(genai_errors.ServerError):
            tutor.generate_content(["hi"], max_attempts=3)
        assert len(client.models.calls) == 3


class Response:
    def __init__(self, text=None, block_reason=None, finish_reason=None):
        self._text = text
        self.prompt_feedback = (
            type("PF", (), {"block_reason": block_reason})() if block_reason else None
        )
        self.candidates = (
            [type("C", (), {"finish_reason": finish_reason})()] if finish_reason else []
        )

    @property
    def text(self):
        return self._text


class TestResponseText:
    def test_returns_the_text(self):
        assert tutor.response_text(Response(text="a lesson")) == "a lesson"

    def test_safety_block_is_reported_specifically(self):
        with pytest.raises(tutor.ModelResponseError, match="safety filter"):
            tutor.response_text(Response(block_reason="SAFETY"))

    def test_empty_text_reports_the_finish_reason(self):
        # google-genai returns None where the old SDK raised, so this branch
        # is now the one an empty candidate list lands in.
        with pytest.raises(tutor.ModelResponseError, match="MAX_TOKENS"):
            tutor.response_text(Response(text=None, finish_reason="MAX_TOKENS"))

    def test_whitespace_only_is_treated_as_empty(self):
        with pytest.raises(tutor.ModelResponseError, match="empty"):
            tutor.response_text(Response(text="   "))
