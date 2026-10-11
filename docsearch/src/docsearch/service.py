from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from docsearch.config import AppConfig, load_config
from docsearch.indexer import VectorIndexer
from docsearch.parser import MarkdownParser


class SearchResultItem(BaseModel):
    filepath: str
    filename: str
    heading: str
    start_line: int
    end_line: int
    content: str
    score: float


class SearchResponse(BaseModel):
    query: str
    total: int
    results: List[SearchResultItem]


class IndexStats(BaseModel):
    total_files: int
    total_chunks: int
    model_name: str
    db_dir: str


class SearchService:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or load_config()
        base_data_dir = Path(self.config.storage.db_path)
        self.indexer = VectorIndexer(
            db_dir=base_data_dir,
            model_name=self.config.model.embedding_model,
        )
        self.parser = MarkdownParser(
            max_chunk_chars=self.config.indexing.chunk_max_characters,
            chunk_overlap=self.config.indexing.chunk_overlap,
        )

    def is_file_allowed(self, path: Path) -> bool:
        if not path.is_file():
            return False
        # 扩展名校验
        if path.suffix.lower() not in [
            ext.lower() for ext in self.config.indexing.file_extensions
        ]:
            return False
        # 排除规则
        path_str = str(path).replace("\\", "/")
        for pattern in self.config.indexing.exclude_patterns:
            if f"/{pattern}/" in path_str or path_str.endswith(f"/{pattern}") or pattern in path.parts:
                return False
        return True

    def index_single_file(self, filepath: Path, force: bool = False) -> int:
        if not self.is_file_allowed(filepath):
            return 0
        if not force and not self.indexer.should_reindex(filepath):
            return 0
        chunks = self.parser.parse_file(filepath)
        count = self.indexer.upsert_chunks(filepath, chunks)
        return count

    def delete_single_file(self, filepath: Path) -> None:
        self.indexer.delete_file(filepath)

    def index_all(self, force: bool = False) -> Dict[str, int]:
        indexed_files = 0
        indexed_chunks = 0
        for watch_dir_str in self.config.indexing.watch_dirs:
            watch_dir = Path(watch_dir_str)
            if not watch_dir.exists():
                continue
            for file_path in watch_dir.rglob("*"):
                if self.is_file_allowed(file_path):
                    count = self.index_single_file(file_path, force=force)
                    if count > 0:
                        indexed_files += 1
                        indexed_chunks += count
        return {
            "indexed_files": indexed_files,
            "indexed_chunks": indexed_chunks,
        }

    def search(self, query: str, top_k: int = 5) -> SearchResponse:
        raw_results = self.indexer.search(query, top_k=top_k)
        items = [SearchResultItem.model_validate(r) for r in raw_results]
        return SearchResponse(
            query=query,
            total=len(items),
            results=items,
        )

    def get_stats(self) -> IndexStats:
        raw = self.indexer.get_stats()
        return IndexStats.model_validate(raw)