import pytest
from app.exceptions import UnknownDocumentError
from app.knowledge import (
    KnowledgeDocument,
    KnowledgeSearchResult,
    load_knowledge_documents,
    KnowledgeRepository,
    search_engineering_knowledge,
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

def test_exact_machine_retrieval_and_metadata():
    results = search_engineering_knowledge(machine_id="M17")
    assert len(results) > 0
    doc_ids = [r.document_id for r in results]
    assert "KB-MAN-M17" in doc_ids
    for r in results:
        assert "M17" in r.machine_ids
        assert isinstance(r.station_ids, list)
        assert isinstance(r.line_ids, list)

def test_topic_filtering():
    results = search_engineering_knowledge(topic="quality", limit=10)
    assert len(results) > 0
    for r in results:
        assert any("quality" in t.lower() for t in r.matched_topics)

def test_machine_and_topic_filtering_together():
    # M17 + welding should return M17 manual (KB-MAN-M17)
    welding_results = search_engineering_knowledge(machine_id="M17", topic="welding")
    assert len(welding_results) > 0
    for r in welding_results:
        assert "M17" in r.machine_ids
        assert any("welding" in t.lower() for t in r.matched_topics)
    assert any(r.document_id == "KB-MAN-M17" for r in welding_results)

    # M17 + incident_report should return M17 incident report (KB-INC-001)
    incident_results = search_engineering_knowledge(machine_id="M17", topic="incident_report")
    assert len(incident_results) > 0
    for r in incident_results:
        assert "M17" in r.machine_ids
        assert any("incident" in t.lower() for t in r.matched_topics)
    assert any(r.document_id == "KB-INC-001" for r in incident_results)

    # Incompatible filter combination returns empty list without hallucination
    unmatched = search_engineering_knowledge(machine_id="M01", topic="laser")
    assert len(unmatched) == 0

def test_document_type_filtering():
    manuals = search_engineering_knowledge(document_type="machine_manual", limit=10)
    assert len(manuals) > 0
    for m in manuals:
        assert m.document_type == "machine_manual"

    incidents = search_engineering_knowledge(document_type="incident_report", limit=10)
    assert len(incidents) > 0
    for inc in incidents:
        assert inc.document_type == "incident_report"

def test_query_keyword_ranking_prefers_specific_matches():
    results = search_engineering_knowledge(query="M17 servo motor overheating thermal shutdown", limit=5)
    assert len(results) > 0
    # Top result should specifically be KB-INC-001 (Incident Report: M17 Servo Motor Overheating)
    assert results[0].document_id == "KB-INC-001"
    assert results[0].relevance_score > 20.0

def test_deterministic_ordering_and_repeated_executions():
    res1 = search_engineering_knowledge(query="hydraulic pressure seal inspection", limit=5)
    res2 = search_engineering_knowledge(query="hydraulic pressure seal inspection", limit=5)
    res3 = search_engineering_knowledge(query="hydraulic pressure seal inspection", limit=5)

    assert len(res1) == len(res2) == len(res3)
    for r1, r2, r3 in zip(res1, res2, res3):
        assert r1.document_id == r2.document_id == r3.document_id
        assert r1.relevance_score == r2.relevance_score == r3.relevance_score
        assert r1.excerpt == r2.excerpt == r3.excerpt

def test_tie_breaking_by_document_id():
    # Filter with equal scores for documents matching the same document_type without extra query terms
    results = search_engineering_knowledge(document_type="incident_report", limit=10)
    assert len(results) >= 2
    # Verify that if two results have the same relevance_score, they are ordered alphabetically by document_id
    for i in range(len(results) - 1):
        if results[i].relevance_score == results[i + 1].relevance_score:
            assert results[i].document_id < results[i + 1].document_id

def test_evidence_metadata_fields_populated():
    results = search_engineering_knowledge(query="M05 robotic welding manual", limit=3)
    assert len(results) > 0
    top = results[0]
    assert top.document_id == "KB-MAN-M05"
    assert top.title is not None
    assert top.document_type == "machine_manual"
    assert "M05" in top.machine_ids
    assert "STA_A2" in top.station_ids
    assert "LINE_A" in top.line_ids
    assert top.source_type == "synthetic"
    assert len(top.matched_topics) > 0
    assert len(top.excerpt) > 0

def test_excerpt_generation_is_bounded_and_contextual():
    results = search_engineering_knowledge(query="laser optics cleaning recalibration", limit=1)
    assert len(results) == 1
    excerpt = results[0].excerpt
    assert isinstance(excerpt, str)
    assert len(excerpt) > 0
    assert len(excerpt) <= 260
    assert not excerpt.startswith("#")
    assert any(term in excerpt.lower() for term in ["laser", "cleaning", "recalibration", "optics"])

def test_empty_search_behavior():
    assert search_engineering_knowledge() == []
    assert search_engineering_knowledge(query="") == []
    assert search_engineering_knowledge(query="   ") == []

def test_weak_and_no_match_behavior():
    # Non-existent query keyword
    assert search_engineering_knowledge(query="nonexistent_alien_technology_xyz_999") == []

    # Non-existent machine ID filter
    assert search_engineering_knowledge(machine_id="M999") == []

    # Non-existent topic filter
    assert search_engineering_knowledge(topic="space_exploration") == []

    # min_score threshold excludes below-threshold matches
    assert search_engineering_knowledge(query="welding", min_score=999.0) == []
