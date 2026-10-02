import os
import logging
from pathlib import Path
from typing import List
import yaml

from app.knowledge.models import KnowledgeDocument

logger = logging.getLogger(__name__)

def parse_markdown_document(file_path: Path) -> KnowledgeDocument:
    """
    Parse a Markdown file with YAML frontmatter into a KnowledgeDocument object.
    """
    content_text = file_path.read_text(encoding="utf-8")
    
    if not content_text.startswith("---"):
        raise ValueError(f"Document {file_path.name} missing starting '---' YAML frontmatter fence.")
        
    parts = content_text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Document {file_path.name} has malformed YAML frontmatter.")
        
    frontmatter_raw = parts[1]
    markdown_body = parts[2].strip()
    
    metadata = yaml.safe_load(frontmatter_raw) or {}
    
    return KnowledgeDocument(
        document_id=metadata.get("document_id", file_path.stem),
        title=metadata.get("title", file_path.stem),
        document_type=metadata.get("document_type", "uncategorized"),
        content=markdown_body,
        machine_ids=metadata.get("machine_ids", []),
        station_ids=metadata.get("station_ids", []),
        line_ids=metadata.get("line_ids", []),
        topics=metadata.get("topics", []),
        source_type=metadata.get("source_type", "synthetic"),
    )

def load_knowledge_documents(documents_dir: Optional[Path] = None) -> List[KnowledgeDocument]:
    """
    Discover and load all Markdown knowledge documents under the specified directory.
    """
    if documents_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent
        documents_dir = base_dir / "knowledge" / "documents"
        
    if not documents_dir.exists():
        logger.warning(f"Knowledge documents directory {documents_dir} does not exist.")
        return []

    documents: List[KnowledgeDocument] = []
    for root, _, files in os.walk(documents_dir):
        for file in files:
            if file.endswith(".md"):
                file_path = Path(root) / file
                try:
                    doc = parse_markdown_document(file_path)
                    documents.append(doc)
                except Exception as e:
                    logger.error(f"Error loading knowledge document {file_path}: {e}")

    logger.info(f"Loaded {len(documents)} knowledge document(s) from {documents_dir}")
    return documents
