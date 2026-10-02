from typing import List, Optional
from pydantic import BaseModel, ConfigDict

class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: str
    title: str
    document_type: str
    content: str
    machine_ids: List[str] = []
    station_ids: List[str] = []
    line_ids: List[str] = []
    topics: List[str] = []
    source_type: str = "synthetic"

class KnowledgeSearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: str
    title: str
    document_type: str
    relevance_score: float
    matched_topics: List[str] = []
    excerpt: str
    source_type: str = "synthetic"
    machine_ids: List[str] = []
    station_ids: List[str] = []
    line_ids: List[str] = []
