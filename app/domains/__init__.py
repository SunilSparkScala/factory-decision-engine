"""
Industry domain selection.

The active industry pack is chosen per request (the UI sends an `X-FDE-Domain` header) and kept in
a ContextVar, so concurrent viewers can use different industries on the same server. Code that
runs outside the request context (e.g. a library-managed thread) can fall back to a scoped
process-wide override; otherwise the configured default applies.
"""
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from app.domains.packs import PACKS

BASE_DIR = Path(__file__).resolve().parent.parent.parent
GENERATED_DATA_DIR = BASE_DIR / "data" / "generated"
KNOWLEDGE_ROOT = BASE_DIR / "knowledge" / "documents"

DEFAULT_DOMAIN = os.getenv("FDE_DEFAULT_DOMAIN", "electronics")
if DEFAULT_DOMAIN not in PACKS:
    DEFAULT_DOMAIN = "electronics"

_current: ContextVar[Optional[str]] = ContextVar("fde_domain", default=None)
_override: Optional[str] = None
_override_lock = threading.Lock()


def normalize(key: Optional[str]) -> Optional[str]:
    key = (key or "").strip().lower()
    return key if key in PACKS else None


def current_domain() -> str:
    return _current.get() or _override or DEFAULT_DOMAIN


def set_domain(key: Optional[str]):
    """Set the domain for the current context; returns a token for `reset_domain`."""
    return _current.set(normalize(key) or DEFAULT_DOMAIN)


def reset_domain(token) -> None:
    _current.reset(token)


@contextmanager
def use_domain(key: str) -> Iterator[None]:
    token = set_domain(key)
    try:
        yield
    finally:
        reset_domain(token)


@contextmanager
def process_override(key: str) -> Iterator[None]:
    """
    Make `key` the domain for threads that don't inherit our context (e.g. the ADK runner's
    worker thread). Serialized with a lock so concurrent requests can't interleave domains.
    """
    global _override
    with _override_lock:
        _override = normalize(key) or DEFAULT_DOMAIN
        try:
            yield
        finally:
            _override = None


def get_pack(key: Optional[str] = None) -> Dict[str, Any]:
    return PACKS[normalize(key) or current_domain()]


def db_path_for(key: Optional[str] = None) -> Path:
    return GENERATED_DATA_DIR / f"factory_{normalize(key) or current_domain()}.db"


def knowledge_dir_for(key: Optional[str] = None) -> Path:
    return KNOWLEDGE_ROOT / (normalize(key) or current_domain())


def operation_profile(op_code: str) -> Optional[Dict[str, Any]]:
    """Look up an operation (e.g. WELDING, PICK_AND_PLACE) across all packs."""
    for pack in PACKS.values():
        for op in pack["operations"]:
            if op["code"] == op_code:
                return op
    return None


def operation_of(machine_type: str) -> str:
    """'PICK_AND_PLACE_Unit_2' -> 'PICK_AND_PLACE'."""
    return machine_type.rsplit("_Unit_", 1)[0].upper()


def public_pack(key: Optional[str] = None) -> Dict[str, Any]:
    """Pack fields the UI needs."""
    p = get_pack(key)
    return {
        "key": p["key"],
        "label": p["label"],
        "plant": p["plant"],
        "unit": p["unit"],
        "examples": p["examples"],
        "operations": [{"code": o["code"], "display": o["display"], "noun": o["noun"]} for o in p["operations"]],
    }
