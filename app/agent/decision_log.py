"""
Append-only audit log of human-approved decisions, separate from the factory database,
which stays immutable.

Storage:
- FDE_LOG_BUCKET set (Cloud Run): one JSON object in Cloud Storage, so the log survives
  restarts and is shared by all instances. Writes use a generation precondition, so two
  instances approving at the same moment can't overwrite each other's entries.
- Otherwise: in memory, mirrored to data/generated/decision_log.json (best effort).
"""
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import config

logger = logging.getLogger(__name__)

LOG_PATH: Path = config.GENERATED_DATA_DIR / "decision_log.json"
LOG_OBJECT = os.environ.get("FDE_LOG_OBJECT", "decision_log.json")
_lock = threading.Lock()
_entries: Optional[List[Dict[str, Any]]] = None
_bucket = None


def _bucket_name() -> str:
    return os.environ.get("FDE_LOG_BUCKET", "").strip()


def _get_bucket():
    global _bucket
    if _bucket is None:
        from google.cloud import storage
        _bucket = storage.Client().bucket(_bucket_name())
    return _bucket


def _read_remote() -> Tuple[List[Dict[str, Any]], int]:
    """Current entries and the object's generation (0 when it doesn't exist yet)."""
    from google.api_core.exceptions import NotFound

    blob = _get_bucket().blob(LOG_OBJECT)
    try:
        data = blob.download_as_bytes()
    except NotFound:
        return [], 0
    return json.loads(data or b"[]"), blob.generation or 0


def _record_remote(entry: Dict[str, Any]) -> None:
    from google.api_core.exceptions import PreconditionFailed

    for _ in range(5):
        entries, generation = _read_remote()
        entries.insert(0, entry)
        try:
            _get_bucket().blob(LOG_OBJECT).upload_from_string(
                json.dumps(entries, indent=2, default=str),
                content_type="application/json",
                if_generation_match=generation,
            )
            return
        except PreconditionFailed:
            continue  # another instance wrote first: re-read and retry
    raise RuntimeError("decision log kept changing during write")


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
        entry = {"decision_id": f"DEC-{uuid.uuid4().hex[:8].upper()}", **entry}
        _load().insert(0, entry)
        if _bucket_name():
            try:
                _record_remote(entry)
                return entry
            except Exception as e:
                logger.warning(f"Could not write decision log to Cloud Storage, keeping it locally: {e}")
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            LOG_PATH.write_text(json.dumps(_entries, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not persist decision log: {e}")
        return entry


def list_entries(limit: int = 50) -> List[Dict[str, Any]]:
    with _lock:
        if _bucket_name():
            try:
                return _read_remote()[0][:limit]
            except Exception as e:
                logger.warning(f"Could not read decision log from Cloud Storage, showing local copy: {e}")
        return list(_load()[:limit])
