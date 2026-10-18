"""Decision log storage: local file by default, Cloud Storage when FDE_LOG_BUCKET is set, Firestore when FDE_LOG_FIRESTORE is set."""
import json

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed

from app.agent import decision_log


class FakeBlob:
    def __init__(self, store, name):
        self.store, self.name, self.generation = store, name, None

    def download_as_bytes(self):
        if self.name not in self.store.objects:
            raise NotFound("missing")
        data, self.generation = self.store.objects[self.name]
        return data

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        current_data, current = self.store.objects.get(self.name, (b"[]", 0))
        if self.store.race_once:
            # another instance appends its entry between our read and our write
            self.store.race_once = False
            other = [{"decision_id": "DEC-OTHER"}] + json.loads(current_data)
            current += 1
            self.store.objects[self.name] = (json.dumps(other).encode(), current)
        if if_generation_match is not None and if_generation_match != current:
            raise PreconditionFailed("generation mismatch")
        self.store.objects[self.name] = (data.encode() if isinstance(data, str) else data, current + 1)


class FakeBucket:
    def __init__(self):
        self.objects, self.race_once = {}, False

    def blob(self, name):
        return FakeBlob(self, name)


@pytest.fixture
def fresh_log(tmp_path, monkeypatch):
    monkeypatch.setattr(decision_log, "LOG_PATH", tmp_path / "decision_log.json")
    monkeypatch.setattr(decision_log, "_entries", None)
    monkeypatch.setattr(decision_log, "_bucket", None)
    return tmp_path


def test_local_file_when_no_bucket(fresh_log, monkeypatch):
    monkeypatch.delenv("FDE_LOG_BUCKET", raising=False)
    entry = decision_log.record({"plan_id": "PLAN_TRANSFER_07"})
    assert (fresh_log / "decision_log.json").exists()
    assert decision_log.list_entries()[0]["decision_id"] == entry["decision_id"]


def test_cloud_storage_shared_and_race_safe(fresh_log, monkeypatch):
    bucket = FakeBucket()
    monkeypatch.setenv("FDE_LOG_BUCKET", "fde-test")
    monkeypatch.setattr(decision_log, "_bucket", bucket)

    first = decision_log.record({"plan_id": "PLAN_TRANSFER_07"})
    bucket.race_once = True  # another instance approves at the same moment
    second = decision_log.record({"plan_id": "PLAN_OVERTIME_01"})

    ids = [e["decision_id"] for e in decision_log.list_entries()]
    assert ids == [second["decision_id"], "DEC-OTHER", first["decision_id"]]  # no entry lost
    assert not (fresh_log / "decision_log.json").exists()  # nothing on the container disk


def test_cloud_storage_failure_falls_back_to_local(fresh_log, monkeypatch):
    monkeypatch.setenv("FDE_LOG_BUCKET", "fde-test")

    def broken():
        raise RuntimeError("no credentials")

    monkeypatch.setattr(decision_log, "_get_bucket", broken)
    entry = decision_log.record({"plan_id": "PLAN_TRANSFER_07"})
    assert (fresh_log / "decision_log.json").exists()
    assert decision_log.list_entries()[0]["decision_id"] == entry["decision_id"]


class FakeCollection:
    """Just enough of a Firestore collection: document(id).set(), order_by().limit().stream()."""

    def __init__(self):
        self.docs, self.fail = {}, False

    def document(self, doc_id):
        coll = self

        class _Doc:
            def set(self, data):
                if coll.fail:
                    raise RuntimeError("Firestore unavailable")
                coll.docs[doc_id] = dict(data)
        return _Doc()

    def order_by(self, field, direction=None):
        if self.fail:
            raise RuntimeError("Firestore unavailable")
        rows = sorted(self.docs.values(), key=lambda d: d[field], reverse=True)

        class _Query:
            def limit(self, n):
                self.rows = rows[:n]
                return self

            def stream(self):
                return [type("Snap", (), {"to_dict": lambda s, r=r: r})() for r in self.rows]
        return _Query()


def test_firestore_one_document_per_decision(fresh_log, monkeypatch):
    coll = FakeCollection()
    monkeypatch.setenv("FDE_LOG_FIRESTORE", "decisions")
    monkeypatch.setattr(decision_log, "_get_collection", lambda: coll)

    first = decision_log.record({"plan_id": "PLAN_TRANSFER_07", "approved_by": "Pradeep", "steps": [["a", "b"]]})
    second = decision_log.record({"plan_id": "PLAN_OVERTIME_01", "approved_by": "Priya"})

    assert set(coll.docs) == {first["decision_id"], second["decision_id"]}
    doc = coll.docs[first["decision_id"]]
    assert doc["approved_by"] == "Pradeep" and doc["plan_id"] == "PLAN_TRANSFER_07"
    listed = decision_log.list_entries()
    assert [e["decision_id"] for e in listed] == [second["decision_id"], first["decision_id"]]  # newest first
    assert listed[1]["steps"] == [["a", "b"]]  # nested lists survive (kept as JSON text)
    assert not (fresh_log / "decision_log.json").exists()  # nothing on the container disk


def test_firestore_keeps_a_cloud_storage_backup(fresh_log, monkeypatch):
    coll, bucket = FakeCollection(), FakeBucket()
    monkeypatch.setenv("FDE_LOG_FIRESTORE", "decisions")
    monkeypatch.setenv("FDE_LOG_BUCKET", "fde-test")
    monkeypatch.setattr(decision_log, "_get_collection", lambda: coll)
    monkeypatch.setattr(decision_log, "_bucket", bucket)

    entry = decision_log.record({"plan_id": "PLAN_TRANSFER_07"})
    assert entry["decision_id"] in coll.docs
    backup = json.loads(bucket.objects[decision_log.LOG_OBJECT][0])
    assert backup[0]["decision_id"] == entry["decision_id"]


def test_firestore_failure_falls_back_to_cloud_storage(fresh_log, monkeypatch):
    coll, bucket = FakeCollection(), FakeBucket()
    coll.fail = True
    monkeypatch.setenv("FDE_LOG_FIRESTORE", "decisions")
    monkeypatch.setenv("FDE_LOG_BUCKET", "fde-test")
    monkeypatch.setattr(decision_log, "_get_collection", lambda: coll)
    monkeypatch.setattr(decision_log, "_bucket", bucket)

    entry = decision_log.record({"plan_id": "PLAN_TRANSFER_07"})
    assert decision_log.list_entries()[0]["decision_id"] == entry["decision_id"]  # read from Cloud Storage


def test_firestore_failure_without_bucket_falls_back_to_local(fresh_log, monkeypatch):
    coll = FakeCollection()
    coll.fail = True
    monkeypatch.setenv("FDE_LOG_FIRESTORE", "decisions")
    monkeypatch.setattr(decision_log, "_get_collection", lambda: coll)

    entry = decision_log.record({"plan_id": "PLAN_TRANSFER_07"})
    assert (fresh_log / "decision_log.json").exists()
    assert decision_log.list_entries()[0]["decision_id"] == entry["decision_id"]
