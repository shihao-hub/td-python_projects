import hashlib
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import lancedb
import pyarrow as pa
from fastembed import TextEmbedding
from docsearch.parser import DocumentChunk


CHUNKS_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("filepath", pa.string()),
        pa.field("filename", pa.string()),
        pa.field("heading", pa.string()),
        pa.field("start_line", pa.int32()),
        pa.field("end_line", pa.int32()),
        pa.field("content", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), 512)),
        pa.field("updated_at", pa.float64()),
    ]
)

META_SCHEMA = pa.schema(
    [
        pa.field("filepath", pa.string()),
        pa.field("mtime", pa.float64()),
        pa.field("content_hash", pa.string()),
        pa.field("chunks_count", pa.int32()),
        pa.field("last_indexed", pa.float64()),
    ]
)


class VectorIndexer:
    def __init__(self, db_dir: Path, model_name: str = "BAAI/bge-small-zh-v1.5"):
        self.db_dir = db_dir
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.db_dir))
        self.model_name = model_name
        self._embedding_model: Optional[TextEmbedding] = None
        self._init_tables()

    @property
    def embedding_model(self) -> TextEmbedding:
        if self._embedding_model is None:
            self._embedding_model = TextEmbedding(model_name=self.model_name)
        return self._embedding_model

    def _init_tables(self):
        tables = self.db.table_names()
        if "chunks" not in tables:
            self.chunks_table = self.db.create_table(
                "chunks", schema=CHUNKS_SCHEMA, mode="create"
            )
        else:
            self.chunks_table = self.db.open_table("chunks")

        if "meta" not in tables:
            self.meta_table = self.db.create_table(
                "meta", schema=META_SCHEMA, mode="create"
            )
        else:
            self.meta_table = self.db.open_table("meta")

    def get_file_meta(self, filepath: str) -> Optional[Dict[str, Any]]:
        norm_path = str(Path(filepath).resolve()).replace("\\", "/")
        try:
            # 搜索元数据
            res = (
                self.meta_table.search()
                .where(f'filepath = "{norm_path}"')
                .limit(1)
                .to_list()
            )
            return res[0] if res else None
        except Exception:
            return None

    def should_reindex(self, filepath: Path) -> bool:
        norm_path = str(filepath.resolve()).replace("\\", "/")
        if not filepath.exists():
            return False
        meta = self.get_file_meta(norm_path)
        if not meta:
            return True
        cur_mtime = filepath.stat().st_mtime
        if cur_mtime > meta["mtime"]:
            return True
        return False

    def upsert_chunks(
        self, filepath: Path, chunks: List[DocumentChunk]
    ) -> int:
        norm_path = str(filepath.resolve()).replace("\\", "/")
        now = time.time()
        mtime = filepath.stat().st_mtime if filepath.exists() else now

        # 1. 清理旧 chunks
        try:
            self.chunks_table.delete(f'filepath = "{norm_path}"')
        except Exception:
            pass

        if not chunks:
            # 更新元数据为 0 chunks
            self._update_meta(norm_path, mtime, "", 0, now)
            return 0

        # 2. 生成 embeddings
        texts = [c.embed_text for c in chunks]
        embeddings = list(self.embedding_model.embed(texts))

        # 3. 准备数据写入
        records = []
        for c, emb in zip(chunks, embeddings):
            chunk_id_str = hashlib.md5(
                f"{norm_path}:{c.chunk_id}:{c.start_line}".encode("utf-8")
            ).hexdigest()
            records.append(
                {
                    "id": chunk_id_str,
                    "filepath": norm_path,
                    "filename": c.filename,
                    "heading": c.heading,
                    "start_line": int(c.start_line),
                    "end_line": int(c.end_line),
                    "content": c.content,
                    "vector": emb.tolist() if hasattr(emb, "tolist") else list(emb),
                    "updated_at": float(now),
                }
            )

        self.chunks_table.add(records)

        # 4. 更新元数据
        content_hash = hashlib.md5(
            chunks[0].content[:200].encode("utf-8")
        ).hexdigest()
        self._update_meta(norm_path, mtime, content_hash, len(records), now)
        return len(records)

    def _update_meta(
        self,
        norm_path: str,
        mtime: float,
        content_hash: str,
        chunks_count: int,
        last_indexed: float,
    ):
        try:
            self.meta_table.delete(f'filepath = "{norm_path}"')
        except Exception:
            pass

        self.meta_table.add(
            [
                {
                    "filepath": norm_path,
                    "mtime": float(mtime),
                    "content_hash": str(content_hash),
                    "chunks_count": int(chunks_count),
                    "last_indexed": float(last_indexed),
                }
            ]
        )

    def delete_file(self, filepath: Path) -> None:
        norm_path = str(filepath.resolve()).replace("\\", "/")
        try:
            self.chunks_table.delete(f'filepath = "{norm_path}"')
            self.meta_table.delete(f'filepath = "{norm_path}"')
        except Exception:
            pass

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        if not query.strip():
            return []

        query_vec = list(self.embedding_model.embed([query]))[0]
        results = (
            self.chunks_table.search(query_vec)
            .metric("cosine")
            .limit(top_k)
            .to_list()
        )
        output = []
        for r in results:
            # LanceDB cosine 距离越小越相似，转换成相似度百分比
            dist = r.get("_distance", 1.0)
            score = max(0.0, 1.0 - float(dist))
            output.append(
                {
                    "filepath": r["filepath"],
                    "filename": r["filename"],
                    "heading": r["heading"],
                    "start_line": r["start_line"],
                    "end_line": r["end_line"],
                    "content": r["content"],
                    "score": round(score, 4),
                }
            )
        return output

    def get_stats(self) -> Dict[str, Any]:
        try:
            total_chunks = self.chunks_table.count_rows()
            total_files = self.meta_table.count_rows()
        except Exception:
            total_chunks = 0
            total_files = 0
        return {
            "total_files": total_files,
            "total_chunks": total_chunks,
            "model_name": self.model_name,
            "db_dir": str(self.db_dir),
        }