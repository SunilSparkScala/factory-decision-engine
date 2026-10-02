import logging
from pathlib import Path
from typing import List, Dict, Optional, Set

from app.exceptions import UnknownDocumentError
from app.knowledge.models import KnowledgeDocument, KnowledgeSearchResult
from app.knowledge.loader import load_knowledge_documents

logger = logging.getLogger(__name__)

class KnowledgeRepository:
    def __init__(self, documents_dir: Optional[Path] = None):
        self.documents_dir = documents_dir
        self.documents: Dict[str, KnowledgeDocument] = {}
        self.load_documents()

    def load_documents(self) -> None:
        docs = load_knowledge_documents(self.documents_dir)
        self.documents = {doc.document_id: doc for doc in docs}
        logger.info(f"KnowledgeRepository loaded {len(self.documents)} document(s).")

    def get_document(self, document_id: str) -> KnowledgeDocument:
        if document_id not in self.documents:
            raise UnknownDocumentError(f"Knowledge document '{document_id}' not found.")
        return self.documents[document_id]

    def search_by_machine(self, machine_id: str) -> List[KnowledgeSearchResult]:
        results: List[KnowledgeSearchResult] = []
        for doc in self.documents.values():
            if machine_id in doc.machine_ids:
                results.append(
                    KnowledgeSearchResult(
                        document_id=doc.document_id,
                        title=doc.title,
                        document_type=doc.document_type,
                        relevance_score=10.0,
                        matched_topics=doc.topics,
                        excerpt=self._make_excerpt(doc.content, machine_id),
                        source_type=doc.source_type,
                    )
                )
        return sorted(results, key=lambda r: (-r.relevance_score, r.document_id))

    def search_by_station(self, station_id: str) -> List[KnowledgeSearchResult]:
        results: List[KnowledgeSearchResult] = []
        for doc in self.documents.values():
            if station_id in doc.station_ids:
                results.append(
                    KnowledgeSearchResult(
                        document_id=doc.document_id,
                        title=doc.title,
                        document_type=doc.document_type,
                        relevance_score=5.0,
                        matched_topics=doc.topics,
                        excerpt=self._make_excerpt(doc.content, station_id),
                        source_type=doc.source_type,
                    )
                )
        return sorted(results, key=lambda r: (-r.relevance_score, r.document_id))

    def search_by_topic(self, topic: str) -> List[KnowledgeSearchResult]:
        topic_clean = topic.lower().strip()
        results: List[KnowledgeSearchResult] = []
        for doc in self.documents.values():
            matched = [t for t in doc.topics if topic_clean in t.lower()]
            if matched:
                results.append(
                    KnowledgeSearchResult(
                        document_id=doc.document_id,
                        title=doc.title,
                        document_type=doc.document_type,
                        relevance_score=4.0 * len(matched),
                        matched_topics=matched,
                        excerpt=self._make_excerpt(doc.content, topic),
                        source_type=doc.source_type,
                    )
                )
        return sorted(results, key=lambda r: (-r.relevance_score, r.document_id))

    def search(self, query: str, limit: int = 5) -> List[KnowledgeSearchResult]:
        if not query or not query.strip():
            return []

        terms = [t.lower().strip() for t in query.split() if len(t.strip()) > 1]
        results: List[KnowledgeSearchResult] = []

        for doc in self.documents.values():
            score = 0.0
            matched_topics: Set[str] = set()

            # Machine ID match (+10.0)
            for m_id in doc.machine_ids:
                if m_id.lower() in query.lower():
                    score += 10.0

            # Station ID match (+5.0)
            for s_id in doc.station_ids:
                if s_id.lower() in query.lower():
                    score += 5.0

            # Line ID match (+5.0)
            for l_id in doc.line_ids:
                if l_id.lower() in query.lower():
                    score += 5.0

            # Topic match (+4.0 per matched topic)
            for t in doc.topics:
                t_lower = t.lower()
                for term in terms:
                    if term in t_lower or t_lower in term:
                        score += 4.0
                        matched_topics.add(t)

            # Title match (+3.0 per term)
            title_lower = doc.title.lower()
            for term in terms:
                if term in title_lower:
                    score += 3.0

            # Content match (+1.0 per term)
            content_lower = doc.content.lower()
            for term in terms:
                if term in content_lower:
                    score += 1.0

            if score > 0:
                results.append(
                    KnowledgeSearchResult(
                        document_id=doc.document_id,
                        title=doc.title,
                        document_type=doc.document_type,
                        relevance_score=round(score, 1),
                        matched_topics=sorted(list(matched_topics)),
                        excerpt=self._make_excerpt(doc.content, terms[0] if terms else query),
                        source_type=doc.source_type,
                    )
                )

        # Deterministic ranking: score desc, document_id asc
        sorted_results = sorted(results, key=lambda r: (-r.relevance_score, r.document_id))
        return sorted_results[:limit]

    def _make_excerpt(self, content: str, query_term: str) -> str:
        lines = [line.strip() for line in content.split("\n") if line.strip() and not line.startswith("#")]
        query_clean = str(query_term).lower()
        for line in lines:
            if query_clean in line.lower():
                return line[:250] + ("..." if len(line) > 250 else "")
        if lines:
            return lines[0][:250] + ("..." if len(lines[0]) > 250 else "")
        return content[:250]
