import os

# The deterministic regression suite runs on the automotive pack (the original plant layout);
# set before any app module is imported so config/DB paths resolve to that pack.
os.environ.setdefault("FDE_DEFAULT_DOMAIN", "automotive")

import pytest

_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI")


@pytest.fixture(autouse=True)
def _offline_gemini(request, monkeypatch):
    """
    Keep the suite offline and deterministic: direct Gemini calls (understanding, war room,
    Q&A, documents) report unavailable unless a test is marked 'integration'.
    The ADK runner is unaffected; tests mock it explicitly.
    """
    if request.node.get_closest_marker("integration"):
        yield
        return
    from app.ai.llm import GeminiService

    monkeypatch.setattr(GeminiService, "available", property(lambda self: False))
    # FactoryDecisionAgent writes the key it was given into os.environ; restore the
    # environment afterwards so one test's mock key never leaks into the next test.
    saved = {k: os.environ.get(k) for k in _KEY_VARS}
    for k in _KEY_VARS:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _isolated_decision_log(tmp_path, monkeypatch):
    """Approvals made by tests go to a temporary folder, never into the real decision log."""
    from app.agent import decision_log

    monkeypatch.setattr(decision_log, "LOG_PATH", tmp_path / "decision_log.json")
    monkeypatch.setattr(decision_log, "_entries", None)
    monkeypatch.delenv("FDE_LOG_BUCKET", raising=False)
