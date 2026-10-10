import pytest


@pytest.fixture(autouse=True)
def _offline_gemini(request, monkeypatch):
    """
    Keep the suite offline and deterministic: direct Gemini calls (understanding, war room,
    Q&A, documents) report unavailable unless a test is marked 'integration'.
    The ADK runner loop is unaffected; tests mock it explicitly.
    """
    if request.node.get_closest_marker("integration"):
        return
    from app.ai.llm import GeminiService

    monkeypatch.setattr(GeminiService, "available", property(lambda self: False))
