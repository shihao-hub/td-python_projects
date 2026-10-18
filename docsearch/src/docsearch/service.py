import re
import time
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
    took_ms: float = Field(default=0.0, description="检索耗时(毫秒)")


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
        if path.suffix.lower() not in [
            ext.lower() for ext in self.config.indexing.file_extensions
        ]:
            return False
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

    def search(self, query: str, top_k: int = 15) -> SearchResponse:
        t0 = time.time()
        clean_q = query.strip()
        if not clean_q:
            return SearchResponse(query=query, total=0, results=[], took_ms=0.0)

        # 1. 扩大向量初筛候选池（取 top_k 的 5~6 倍，至少 80 条，防止单文件多片段挤占）
        candidate_k = max(top_k * 5, 80)
        raw_results = self.indexer.search(clean_q, top_k=candidate_k)

        # 2. 提取检索词元（英文词与中文词元）
        tokens = [
            t.lower()
            for t in re.split(r"[\s\-_/]+", clean_q)
            if len(t.strip()) >= 2
        ]

        # 3. 混合重排打分 (Hybrid Ranking) 与去重
        scored_items = []
        seen_chunks = set()

        for r in raw_results:
            chunk_sig = f"{r['filepath']}:{r['start_line']}"
            if chunk_sig in seen_chunks:
                continue
            seen_chunks.add(chunk_sig)

            vec_score = float(r.get("score", 0.0))
            fn_lower = r.get("filename", "").lower()
            content_lower = r.get("content", "").lower()
            heading_lower = (r.get("heading") or "").lower()

            # 词元命中加权（文件名权重更高，正文/标题次之）
            lexical_bonus = 0.0
            if tokens:
                fn_hits = sum(1 for t in tokens if t in fn_lower)
                body_hits = sum(
                    1 for t in tokens if t in content_lower or t in heading_lower
                )
                lexical_bonus += (fn_hits / len(tokens)) * 0.15
                lexical_bonus += (body_hits / len(tokens)) * 0.10

            final_score = min(round(vec_score + lexical_bonus, 4), 1.0)
            item_dict = dict(r)
            item_dict["score"] = final_score
            scored_items.append(item_dict)

        # 按综合得分从高到低排序
        scored_items.sort(key=lambda x: -x["score"])

        # 4. 截取最终 top_k 个片段
        final_items = [
            SearchResultItem.model_validate(it) for it in scored_items[:top_k]
        ]
        took_ms = round((time.time() - t0) * 1000, 2)

        return SearchResponse(
            query=query,
            total=len(final_items),
            results=final_items,
            took_ms=took_ms,
        )

    def get_stats(self) -> IndexStats:
        raw = self.indexer.get_stats()
        return IndexStats.model_validate(raw)