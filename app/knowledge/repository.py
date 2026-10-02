import logging
import re
from pathlib import Path
from typing import List, Dict, Optional, Set

from app.exceptions import UnknownDocumentError
from app.knowledge.models import KnowledgeDocument, KnowledgeSearchResult
from app.knowledge.loader import load_knowledge_documents

logger = logging.getLogger(__name__)

STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can't", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't",
    "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i",
    "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's",
    "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought",
    "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she",
    "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're",
    "they've", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves"
}

def _tokenize(text: str) -> List[str]:
    """Extract clean alphanumeric search tokens, filtering out stopwords and single characters."""
    tokens = re.findall(r"[A-Za-z0-9_-]+", text.lower())
    return [t for t in tokens if len(t) > 1 and t not in STOPWORDS]

def _make_excerpt(
    content: str,
    query_terms: List[str],
    matched_topics: List[str],
    machine_ids: List[str],
    max_chars: int = 250,
) -> str:
    """
    Extract a bounded, highly relevant contextual excerpt containing matching search terms.
    If multiple sections match, deterministically select the densest matching candidate unit.
    """
    raw_blocks = re.split(r"\n\s*\n|\n(?=[-*0-9]+\.|\b[A-Z0-9_-]+:)", content)
    candidate_blocks: List[str] = []
    for b in raw_blocks:
        cleaned = b.strip()
        lines = [line.strip() for line in cleaned.split("\n") if line.strip() and not line.strip().startswith("#")]
        if lines:
            candidate_blocks.append(" ".join(lines))

    if not candidate_blocks:
        candidate_blocks = [line.strip() for line in content.split("\n") if line.strip() and not line.strip().startswith("#")]

    search_targets = set(t.lower() for t in query_terms + matched_topics + machine_ids if t)

    best_block = ""
    best_score = -1

    for block in candidate_blocks:
        block_lower = block.lower()
        score = sum(1 for target in search_targets if target in block_lower)
        if score > best_score:
            best_score = score
            best_block = block

    if not best_block and candidate_blocks:
        best_block = candidate_blocks[0]
    elif not best_block:
        best_block = content.strip()

    best_block = re.sub(r"\s+", " ", best_block).strip()
    if len(best_block) > max_chars:
        return best_block[:max_chars].rstrip() + "..."
    return best_block

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

    def search_by_machine(self, machine_id: str, limit: int = 20) -> List[KnowledgeSearchResult]:
        return self.search(machine_id=machine_id, limit=limit)

    def search_by_station(self, station_id: str, limit: int = 20) -> List[KnowledgeSearchResult]:
        return self.search(station_id=station_id, limit=limit)

    def search_by_topic(self, topic: str, limit: int = 20) -> List[KnowledgeSearchResult]:
        return self.search(topic=topic, limit=limit)

    def search(
        self,
        query: Optional[str] = None,
        machine_id: Optional[str] = None,
        station_id: Optional[str] = None,
        line_id: Optional[str] = None,
        topic: Optional[str] = None,
        document_type: Optional[str] = None,
        limit: int = 5,
        min_score: float = 0.0,
    ) -> List[KnowledgeSearchResult]:
        """
        Deterministic, evidence-centric engineering knowledge retrieval.
        Supports keyword queries, composable metadata filtering, and reproducible ranking.
        """
        query_str = query.strip() if query else ""
        has_query = bool(query_str)
        has_filters = any([machine_id, station_id, line_id, topic, document_type])

        if not has_query and not has_filters:
            return []

        tokens = _tokenize(query_str) if has_query else []
        results: List[KnowledgeSearchResult] = []

        for doc in self.documents.values():
            # 1. Composable Metadata Filtering
            if machine_id:
                m_target = machine_id.strip().upper()
                if m_target not in [m.upper() for m in doc.machine_ids]:
                    continue

            if station_id:
                s_target = station_id.strip().upper()
                if s_target not in [s.upper() for s in doc.station_ids]:
                    continue

            if line_id:
                l_target = line_id.strip().upper()
                if l_target not in [l.upper() for l in doc.line_ids]:
                    continue

            if document_type:
                dt_target = document_type.strip().lower()
                if doc.document_type.lower() != dt_target:
                    continue

            if topic:
                t_target = topic.strip().lower()
                if not any(t_target in t.lower() or t.lower() in t_target for t in doc.topics):
                    continue

            # 2. Deterministic Relevance Scoring
            score = 0.0
            matched_topics: Set[str] = set()

            # Metadata Filter adherence boosts
            if machine_id:
                score += 10.0
            if station_id:
                score += 5.0
            if line_id:
                score += 5.0
            if document_type:
                score += 4.0
            if topic:
                score += 8.0
                for t in doc.topics:
                    if topic.strip().lower() in t.lower() or t.lower() in topic.strip().lower():
                        matched_topics.add(t)

            # Query keyword and entity matching boosts
            if has_query:
                query_lower = query_str.lower()

                # Exact Machine ID Match in query (+12.0)
                for m in doc.machine_ids:
                    if m.lower() in query_lower:
                        score += 12.0

                # Station / Line ID in query (+5.0)
                for s in doc.station_ids:
                    if s.lower() in query_lower:
                        score += 5.0
                for l in doc.line_ids:
                    if l.lower() in query_lower:
                        score += 5.0

                # Topic in query (+6.0 per matched topic)
                for t in doc.topics:
                    t_lower = t.lower()
                    if t_lower in query_lower or any(tok in t_lower for tok in tokens if len(tok) > 2):
                        score += 6.0
                        matched_topics.add(t)

                # Document Type in query (+3.0)
                if doc.document_type.lower() in query_lower or any(tok in doc.document_type.lower() for tok in tokens):
                    score += 3.0

                # Title keyword matches (+4.0 per token in title, +5.0 exact phrase bonus)
                title_lower = doc.title.lower()
                for tok in tokens:
                    if tok in title_lower:
                        score += 4.0
                if query_lower in title_lower:
                    score += 5.0

                # Content keyword matches (+1.0 per unique token present, +0.2 per frequency bonus up to +2.0)
                content_lower = doc.content.lower()
                for tok in tokens:
                    count = content_lower.count(tok)
                    if count > 0:
                        score += 1.0 + min(2.0, (count - 1) * 0.2)

            # 3. Minimum score threshold check (empty/low confidence filtering)
            if score <= 0.0 or score < min_score:
                continue

            # 4. Contextual evidence excerpt generation
            excerpt = _make_excerpt(
                content=doc.content,
                query_terms=tokens,
                matched_topics=list(matched_topics),
                machine_ids=doc.machine_ids,
                max_chars=250,
            )

            results.append(
                KnowledgeSearchResult(
                    document_id=doc.document_id,
                    title=doc.title,
                    document_type=doc.document_type,
                    relevance_score=round(score, 2),
                    matched_topics=sorted(list(matched_topics)),
                    excerpt=excerpt,
                    source_type=doc.source_type,
                    machine_ids=list(doc.machine_ids),
                    station_ids=list(doc.station_ids),
                    line_ids=list(doc.line_ids),
                )
            )

        # 5. Deterministic Ranking: score descending, tie-break by document_id ascending
        sorted_results = sorted(results, key=lambda r: (-r.relevance_score, r.document_id))
        return sorted_results[:limit]

    def search_engineering_knowledge(
        self,
        query: Optional[str] = None,
        machine_id: Optional[str] = None,
        station_id: Optional[str] = None,
        line_id: Optional[str] = None,
        topic: Optional[str] = None,
        document_type: Optional[str] = None,
        limit: int = 5,
        min_score: float = 0.0,
    ) -> List[KnowledgeSearchResult]:
        return self.search(
            query=query,
            machine_id=machine_id,
            station_id=station_id,
            line_id=line_id,
            topic=topic,
            document_type=document_type,
            limit=limit,
            min_score=min_score,
        )

# Module-level singleton helper and tool function
_default_repo: Optional[KnowledgeRepository] = None

def get_knowledge_repository() -> KnowledgeRepository:
    global _default_repo
    if _default_repo is None:
        _default_repo = KnowledgeRepository()
    return _default_repo

def search_engineering_knowledge(
    query: Optional[str] = None,
    machine_id: Optional[str] = None,
    station_id: Optional[str] = None,
    line_id: Optional[str] = None,
    topic: Optional[str] = None,
    document_type: Optional[str] = None,
    limit: int = 5,
    min_score: float = 0.0,
) -> List[KnowledgeSearchResult]:
    """
    Primary interface for engineering evidence retrieval.
    Designed for deterministic execution and future agent tool invocation.
    """
    repo = get_knowledge_repository()
    return repo.search(
        query=query,
        machine_id=machine_id,
        station_id=station_id,
        line_id=line_id,
        topic=topic,
        document_type=document_type,
        limit=limit,
        min_score=min_score,
    )
