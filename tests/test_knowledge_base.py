import pytest
from app.exceptions import UnknownDocumentError
from app.knowledge import (
    KnowledgeDocument,
    KnowledgeSearchResult,
    load_knowledge_documents,
    KnowledgeRepository,
)

def test_all_documents_load_successfully():
    repo = KnowledgeRepository()
    assert len(repo.documents) >= 12

def test_metadata_parsed_correctly():
    repo = KnowledgeRepository()
    doc = repo.get_document("KB-MAN-M17")
    
    assert doc.document_id == "KB-MAN-M17"
    assert "M17" in doc.title
    assert doc.document_type == "machine_manual"
    assert "M17" in doc.machine_ids
    assert "STA_B2" in doc.station_ids
    assert "LINE_B" in doc.line_ids
    assert "welding" in doc.topics
    assert doc.source_type == "synthetic"

def test_every_document_has_unique_id():
    docs = load_knowledge_documents()
    doc_ids = [doc.document_id for doc in docs]
    assert len(doc_ids) == len(set(doc_ids)), "All knowledge document IDs must be unique"

def test_m17_search_returns_m17_knowledge():
    repo = KnowledgeRepository()
    results = repo.search_by_machine("M17")
    
    assert len(results) >= 1
    doc_ids = [r.document_id for r in results]
    assert "KB-MAN-M17" in doc_ids

def test_machine_transfer_query_returns_relevant_documents():
    repo = KnowledgeRepository()
    results = repo.search("quality requirements after machine transfer")
    
    assert len(results) > 0
    doc_ids = [r.document_id for r in results]
    # Should include quality inspection post-transfer document or transfer SOP
    assert any(did in ["KB-QTY-001", "KB-SOP-001", "KB-MNT-004"] for did in doc_ids)

def test_overtime_query_returns_overtime_constraints():
    repo = KnowledgeRepository()
    results = repo.search("overtime recovery constraints")
    
    assert len(results) > 0
    doc_ids = [r.document_id for r in results]
    assert "KB-SOP-003" in doc_ids

def test_topic_search_works():
    repo = KnowledgeRepository()
    results = repo.search_by_topic("welding")
    
    assert len(results) > 0
    for r in results:
        assert isinstance(r, KnowledgeSearchResult)

def test_machine_filtering_works():
    repo = KnowledgeRepository()
    results = repo.search_by_machine("M01")
    
    assert len(results) > 0
    for r in results:
        assert r.document_id in ["KB-MAN-M01", "KB-INC-002"]

def test_missing_document_raises_unknown_document_error():
    repo = KnowledgeRepository()
    with pytest.raises(UnknownDocumentError):
        repo.get_document("KB-NONEXISTENT-999")

def test_search_ordering_is_deterministic():
    repo = KnowledgeRepository()
    res1 = repo.search("machine M17 operating procedure")
    res2 = repo.search("machine M17 operating procedure")
    
    assert len(res1) == len(res2)
    for r1, r2 in zip(res1, res2):
        assert r1.document_id == r2.document_id
        assert r1.relevance_score == r2.relevance_score

def test_search_result_contains_evidence_metadata():
    repo = KnowledgeRepository()
    results = repo.search("machine substitution rules")
    
    assert len(results) > 0
    top = results[0]
    assert top.document_id is not None
    assert top.title is not None
    assert top.document_type is not None
    assert top.relevance_score > 0
    assert isinstance(top.excerpt, str)
    assert len(top.excerpt) > 0

def test_synthetic_source_type_preserved():
    repo = KnowledgeRepository()
    results = repo.search("M17")
    
    for r in results:
        assert r.source_type == "synthetic"
