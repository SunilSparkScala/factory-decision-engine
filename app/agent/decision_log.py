"""
Append-only audit log of human-approved decisions, separate from the factory database,
which stays immutable.

Storage:
- FDE_LOG_FIRESTORE set (Cloud Run): one Firestore document per approved decision in that
  collection, read newest first. If FDE_LOG_BUCKET is also set, Cloud Storage keeps a backup copy.
- FDE_LOG_BUCKET set: one JSON object in Cloud Storage, so the log survives
  restarts and is shared by all instances. Writes use a generation precondition, so two
  instances approving at the same moment can't overwrite each other's entries.
- Otherwise: in memory, mirrored to data/generated/decision_log.json (best effort).
Each store falls back to the next one if it fails, so an approval is never lost.
"""
import json
import logging
import os
import sqlite3
import threading
import time
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
_firestore = None


def _audit_db_path() -> Path:
    return LOG_PATH.parent / "decision_audit.db"


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS approved_decisions (
            decision_id TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            idempotency_key TEXT,
            data_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_decision_fingerprint ON approved_decisions(fingerprint)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_decision_idempotency_key ON approved_decisions(idempotency_key) WHERE idempotency_key IS NOT NULL AND idempotency_key != ''")
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    db_path = _audit_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    _init_db(conn)
    return conn


def _bucket_name() -> str:
    return os.environ.get("FDE_LOG_BUCKET", "").strip()


def _get_bucket():
    global _bucket
    if _bucket is None:
        from google.cloud import storage
        _bucket = storage.Client().bucket(_bucket_name())
    return _bucket


def _collection_name() -> str:
    return os.environ.get("FDE_LOG_FIRESTORE", "").strip()


def _get_collection():
    global _firestore
    if _firestore is None:
        from google.cloud import firestore
        _firestore = firestore.Client(database=os.environ.get("FDE_FIRESTORE_DATABASE", "(default)"))
    return _firestore.collection(_collection_name())


def _record_firestore(entry: Dict[str, Any]) -> None:
    """One document per decision. The full entry is kept as JSON text (Firestore can't store
    nested lists); a few plain fields sit beside it so the log is readable in the console."""
    doc = {
        "decision_id": entry["decision_id"],
        "logged_at": time.time(),
        "entry_json": json.dumps(entry, default=str),
    }
    for key in ("approved_by", "approver", "machine_id", "plan_id", "cost_impact_usd", "domain"):
        if isinstance(entry.get(key), (str, int, float)):
            doc[key] = entry[key]
    _get_collection().document(entry["decision_id"]).set(doc)


def _read_firestore(limit: int) -> List[Dict[str, Any]]:
    from google.cloud import firestore
    query = _get_collection().order_by("logged_at", direction=firestore.Query.DESCENDING).limit(limit)
    return [json.loads(d.to_dict()["entry_json"]) for d in query.stream()]


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
        fingerprint = entry.get("fingerprint") or f"FP-{uuid.uuid4().hex}"
        idempotency_key = entry.get("idempotency_key") or None

        # Check existing in SQLite database
        conn = None
        try:
            conn = _get_conn()
            cursor = conn.cursor()
            clauses = []
            params = []
            if idempotency_key:
                clauses.append("idempotency_key = ?")
                params.append(idempotency_key)
            if fingerprint:
                clauses.append("fingerprint = ?")
                params.append(fingerprint)
            if clauses:
                cursor.execute(
                    f"SELECT data_json FROM approved_decisions WHERE {' OR '.join(clauses)} ORDER BY rowid DESC LIMIT 1",
                    params
                )
                row = cursor.fetchone()
                if row:
                    conn.close()
                    return json.loads(row[0])
        except Exception as e:
            logger.warning(f"Error querying audit database: {e}")

        decision_id = entry.get("decision_id") or f"DEC-{uuid.uuid4().hex[:8].upper()}"
        full_entry = {"decision_id": decision_id, **entry}

        # Atomically insert into SQLite with unique constraints
        if conn is not None:
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO approved_decisions (decision_id, fingerprint, idempotency_key, data_json) VALUES (?, ?, ?, ?)",
                        (decision_id, fingerprint, idempotency_key, json.dumps(full_entry, default=str))
                    )
            except sqlite3.IntegrityError:
                # Concurrent duplicate was committed: fetch it atomically
                cursor = conn.execute(
                    "SELECT data_json FROM approved_decisions WHERE (idempotency_key IS NOT NULL AND idempotency_key = ?) OR fingerprint = ? LIMIT 1",
                    (idempotency_key, fingerprint)
                )
                row = cursor.fetchone()
                conn.close()
                if row:
                    return json.loads(row[0])
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        # Update in-memory and JSON file mirror
        _load().insert(0, full_entry)
        if _collection_name():
            try:
                _record_firestore(full_entry)
                if _bucket_name():
                    try:
                        _record_remote(full_entry)  # backup copy
                    except Exception as e:
                        logger.warning(f"Could not write the Cloud Storage backup of the decision log: {e}")
                return full_entry
            except Exception as e:
                logger.warning(f"Could not write decision log to Firestore, using the next store: {e}")
        if _bucket_name():
            try:
                _record_remote(full_entry)
                return full_entry
            except Exception as e:
                logger.warning(f"Could not write decision log to Cloud Storage, keeping it locally: {e}")
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            LOG_PATH.write_text(json.dumps(_entries, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not persist decision log: {e}")
        return full_entry


def list_entries(limit: int = 50) -> List[Dict[str, Any]]:
    with _lock:
        if _collection_name():
            try:
                return _read_firestore(limit)
            except Exception as e:
                logger.warning(f"Could not read decision log from Firestore, using the next store: {e}")
        if _bucket_name():
            try:
                return _read_remote()[0][:limit]
            except Exception as e:
                logger.warning(f"Could not read decision log from Cloud Storage, showing local copy: {e}")
        try:
            conn = _get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT data_json FROM approved_decisions ORDER BY rowid DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            conn.close()
            if rows:
                return [json.loads(r[0]) for r in rows]
        except Exception as e:
            logger.warning(f"Error reading audit database: {e}")
        return list(_load()[:limit])
