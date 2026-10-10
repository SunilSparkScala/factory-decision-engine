"""
Append-only audit log of human-approved decisions.

Kept in memory and mirrored to data/generated/decision_log.json (best effort), separate from
the factory database, which stays immutable.
"""
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import config

logger = logging.getLogger(__name__)

LOG_PATH: Path = config.GENERATED_DATA_DIR / "decision_log.json"
_lock = threading.Lock()
_entries: Optional[List[Dict[str, Any]]] = None


def _load() -> List[Dict[str, Any]]:
    global _entries
    if _entries is None:
        try:
            _entries = json.loads(LOG_PATH.read_text(encoding="utf-8")) if LOG_PATH.exists() else []
        except Exception as e:
            logger.warning(f"Decision log unreadable, starting empty: {e}")
            _entries = []
    return _entries


def record(entry: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        entries = _load()
        entry = {"decision_id": f"DEC-{uuid.uuid4().hex[:8].upper()}", **entry}
        entries.insert(0, entry)
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            LOG_PATH.write_text(json.dumps(entries, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not persist decision log: {e}")
        return entry


def list_entries(limit: int = 50) -> List[Dict[str, Any]]:
    with _lock:
        return list(_load()[:limit])
