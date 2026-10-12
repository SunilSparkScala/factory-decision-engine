"""
Thin wrapper around the google-genai client used by every generative feature.

Design rules:
- Gemini never produces authoritative numbers; callers pass deterministic data in and get
  language / structured interpretation out.
- Every call is best-effort: on any failure (no key, quota, timeout, bad JSON) the wrapper
  returns None and the caller falls back to a deterministic path.
"""
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "gemini-flash-lite-latest"
DEFAULT_TIMEOUT_MS = 30_000

# Two ways to reach Gemini:
# - Gemini API (AI Studio key): free-tier quotas are per model, so roles default to different
#   Flash-Lite models to spread load (larger Flash models allow only ~20 requests/day there).
# - Vertex AI (GOOGLE_GENAI_USE_VERTEXAI=TRUE, used on Cloud Run): billed to the GCP project, no
#   free-tier caps, authenticated by the service account. Vertex has no "-latest" aliases.
# Any role can be overridden with its environment variable.
ROLE_MODEL_DEFAULTS = {
    #               env var                   AI Studio default            Vertex AI default
    "assistant": ("FDE_MODEL_ASSISTANT", "gemini-flash-lite-latest", "gemini-3.5-flash-lite"),
    "specialists": ("FDE_MODEL_SPECIALISTS", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite"),
}
STUDIO_FALLBACK_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash",
    "gemini-3.5-flash",
]
VERTEX_FALLBACK_MODELS = ["gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-3.5-flash", "gemini-2.5-flash-lite"]
RETRYABLE_MARKERS = ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "overloaded")

# Models that recently hit a quota or overload, mapped to the time they may be retried.
_cooldown_until: Dict[str, float] = {}
_cooldown_lock = threading.Lock()


def vertex_enabled() -> bool:
    return os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in ("true", "1", "yes")


def fallback_models() -> List[str]:
    return VERTEX_FALLBACK_MODELS if vertex_enabled() else STUDIO_FALLBACK_MODELS


def model_for(role: str) -> str:
    env_name, studio_default, vertex_default = ROLE_MODEL_DEFAULTS[role]
    return os.getenv(env_name, vertex_default if vertex_enabled() else studio_default)


def is_retryable(error: Exception) -> bool:
    text = f"{type(error).__name__} {error}"
    return any(m in text for m in RETRYABLE_MARKERS)


def mark_unavailable(model: str, error: Exception) -> None:
    """Remember a quota/overload failure so the next calls skip this model instead of waiting on it."""
    text = str(error)
    if "PerDay" in text:
        seconds = 6 * 3600
    else:
        m = re.search(r"retry in ([\d.]+)s", text)
        seconds = float(m.group(1)) if m else (30.0 if "503" in text or "UNAVAILABLE" in text else 60.0)
    with _cooldown_lock:
        _cooldown_until[model] = time.monotonic() + seconds
    logger.info(f"Gemini model {model} cooling down for {seconds:.0f}s")


def usable_models(chain: List[str]) -> List[str]:
    """The chain without models in cooldown; if every model is cooling down, the one that recovers first."""
    now = time.monotonic()
    with _cooldown_lock:
        ready = [m for m in chain if _cooldown_until.get(m, 0) <= now]
        if ready:
            return ready
        return [min(chain, key=lambda m: _cooldown_until.get(m, 0))]


def chain_for(primary: str) -> List[str]:
    return [primary] + [m for m in fallback_models() if m != primary]


class GeminiService:
    """Best-effort Gemini access for structured and free-text generation."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        self._client = None
        self._client_lock = threading.Lock()
        self.last_error: Optional[str] = None
        self.last_model: Optional[str] = None

    def _model_chain(self) -> list:
        return usable_models(chain_for(self.model))

    @property
    def available(self) -> bool:
        return bool(self.api_key) or vertex_enabled()

    def _get_client(self):
        # Locked so concurrent callers (e.g. parallel specialists) share one client; a discarded
        # duplicate would be closed while another thread is still using it.
        with self._client_lock:
            if self._client is None:
                from google import genai
                from google.genai import types

                http_options = types.HttpOptions(timeout=DEFAULT_TIMEOUT_MS)
                if vertex_enabled():
                    self._client = genai.Client(
                        vertexai=True,
                        project=os.getenv("GOOGLE_CLOUD_PROJECT"),
                        location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
                        http_options=http_options,
                    )
                else:
                    self._client = genai.Client(api_key=self.api_key, http_options=http_options)
            return self._client

    def _generate(self, prompt: str, system: Optional[str], schema: Optional[Type[BaseModel]], temperature: float):
        """Call Gemini, moving down the model chain on rate-limit or overload errors."""
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if schema is not None:
            config.response_mime_type = "application/json"
            config.response_schema = schema
        last_exc: Optional[Exception] = None
        for model in self._model_chain():
            try:
                resp = self._get_client().models.generate_content(model=model, contents=prompt, config=config)
                self.last_model = model
                return resp
            except Exception as e:
                last_exc = e
                if not is_retryable(e):
                    raise
                mark_unavailable(model, e)
        raise last_exc

    def generate_json(
        self,
        prompt: str,
        schema: Type[T],
        system: Optional[str] = None,
        temperature: float = 0.0,
    ) -> Optional[T]:
        """Generate a response validated against a Pydantic schema, or None on any failure."""
        if not self.available:
            self.last_error = "No Gemini API key configured."
            return None
        try:
            resp = self._generate(prompt, system, schema, temperature)
            parsed = getattr(resp, "parsed", None)
            if isinstance(parsed, schema):
                return parsed
            text = (resp.text or "").strip()
            return schema.model_validate(json.loads(text))
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            logger.warning(f"Gemini structured generation failed: {self.last_error}")
            return None

    def generate_text(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.2,
    ) -> Optional[str]:
        """Generate free text, or None on any failure."""
        if not self.available:
            self.last_error = "No Gemini API key configured."
            return None
        try:
            resp = self._generate(prompt, system, None, temperature)
            text = (resp.text or "").strip()
            return text or None
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            logger.warning(f"Gemini text generation failed: {self.last_error}")
            return None


def compact_json(data: Any, limit: int = 12_000) -> str:
    """Serialize context for prompts, truncated to keep requests small."""
    text = json.dumps(data, default=str, separators=(",", ":"))
    return text if len(text) <= limit else text[:limit] + "...(truncated)"
