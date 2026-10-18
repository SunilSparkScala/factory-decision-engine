"""
Voice notes: POST /api/transcribe sends the recording to Gemini and returns the text.
Gemini is always mocked; no test calls the real service.
"""
import pytest
from fastapi.testclient import TestClient

from app import api as api_mod
from app.ai.llm import GeminiService

AUDIO = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64  # stands in for a short recording


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_mod, "_transcriber", None)
    monkeypatch.setattr(GeminiService, "available", property(lambda self: True))
    return TestClient(api_mod.app)


def test_transcribe_returns_gemini_text(client, monkeypatch):
    seen = {}

    def fake(self, audio, mime_type):
        seen.update(audio=audio, mime_type=mime_type)
        return "The pick and place machine on Line B stopped, maybe 6 hours. Protect urgent orders."

    monkeypatch.setattr(GeminiService, "transcribe_audio", fake)
    res = client.post("/api/transcribe", content=AUDIO, headers={"Content-Type": "audio/webm;codecs=opus"})
    assert res.status_code == 200
    data = res.json()
    assert data["source"] == "gemini"
    assert data["text"].startswith("The pick and place machine on Line B stopped")
    assert seen == {"audio": AUDIO, "mime_type": "audio/webm"}  # codec parameter stripped for Gemini


def test_transcribe_normalises_wav_type(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(GeminiService, "transcribe_audio", lambda self, a, m: seen.setdefault("mime", m) and "M17 down")
    res = client.post("/api/transcribe", content=AUDIO, headers={"Content-Type": "audio/x-wav"})
    assert res.status_code == 200
    assert seen["mime"] == "audio/wav"


def test_transcribe_gemini_failure_is_a_clear_error(client, monkeypatch):
    monkeypatch.setattr(GeminiService, "transcribe_audio", lambda self, a, m: None)
    res = client.post("/api/transcribe", content=AUDIO, headers={"Content-Type": "audio/wav"})
    assert res.status_code == 502
    assert "could not transcribe" in res.json()["detail"]


def test_transcribe_without_gemini_configured(monkeypatch):
    monkeypatch.setattr(api_mod, "_transcriber", None)
    monkeypatch.setattr(GeminiService, "available", property(lambda self: False))
    monkeypatch.setattr(GeminiService, "transcribe_audio", lambda self, a, m: pytest.fail("must not call Gemini"))
    res = TestClient(api_mod.app).post("/api/transcribe", content=AUDIO, headers={"Content-Type": "audio/wav"})
    assert res.status_code == 503


def test_transcribe_rejects_too_large_recording(client, monkeypatch):
    monkeypatch.setattr(GeminiService, "transcribe_audio", lambda self, a, m: pytest.fail("must not call Gemini"))
    big = b"\x00" * (api_mod.MAX_AUDIO_BYTES + 1)
    res = client.post("/api/transcribe", content=big, headers={"Content-Type": "audio/wav"})
    assert res.status_code == 413


def test_transcribe_rejects_empty_and_non_audio(client, monkeypatch):
    monkeypatch.setattr(GeminiService, "transcribe_audio", lambda self, a, m: pytest.fail("must not call Gemini"))
    assert client.post("/api/transcribe", content=b"", headers={"Content-Type": "audio/wav"}).status_code == 400
    assert client.post("/api/transcribe", content=b"hello", headers={"Content-Type": "text/plain"}).status_code == 415


def test_transcribe_audio_sends_audio_part_and_reads_no_speech(monkeypatch):
    """The service sends the audio as a multimodal part and maps the no-speech marker to ''."""
    captured = {}

    class Resp:
        def __init__(self, text):
            self.text = text

    llm = GeminiService(model="test-model")
    monkeypatch.setattr(GeminiService, "available", property(lambda self: True))

    def fake_generate(self, contents, system, schema, temperature):
        captured["contents"] = contents
        return Resp(captured.get("reply", '"Transcript: M17 stopped for 6 hours."'))

    monkeypatch.setattr(GeminiService, "_generate", fake_generate)
    assert llm.transcribe_audio(b"abc", "audio/wav") == "M17 stopped for 6 hours."
    part = captured["contents"][0]
    assert part.inline_data.mime_type == "audio/wav" and part.inline_data.data == b"abc"
    assert "M17" in captured["contents"][1]

    captured["reply"] = "NO_SPEECH"
    assert llm.transcribe_audio(b"abc", "audio/wav") == ""
