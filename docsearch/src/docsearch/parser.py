import re
from pathlib import Path
from typing import List
from pydantic import BaseModel, Field


class DocumentChunk(BaseModel):
    chunk_id: int
    filepath: str
    filename: str
    heading: str = ""
    start_line: int = 1
    end_line: int = 1
    content: str
    embed_text: str = ""


class MarkdownParser:
    def __init__(self, max_chunk_chars: int = 800, chunk_overlap: int = 100):
        self.max_chunk_chars = max_chunk_chars
        self.chunk_overlap = chunk_overlap

    def parse_file(self, filepath: Path) -> List[DocumentChunk]:
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []

        filename = filepath.name
        norm_path = str(filepath.resolve())
        lines = text.splitlines()
        if not lines:
            return []

        chunks: List[DocumentChunk] = []
        current_heading = filename
        current_buffer: List[str] = []
        chunk_start_line = 1

        heading_pattern = re.compile(r"^(#{1,4})\s+(.+)$")

        for idx, line in enumerate(lines, start=1):
            match = heading_pattern.match(line.strip())
            if match:
                # 遇到新标题，把之前的缓存打包成 chunk
                if current_buffer:
                    chunk_text = "\n".join(current_buffer).strip()
                    if chunk_text:
                        chunks.extend(
                            self._split_large_text(
                                chunk_text,
                                norm_path,
                                filename,
                                current_heading,
                                chunk_start_line,
                                idx - 1,
                            )
                        )
                    current_buffer = []
                current_heading = match.group(2).strip()
                chunk_start_line = idx

            current_buffer.append(line)

            # 缓存过长时切分
            cur_length = sum(len(l) for l in current_buffer)
            if cur_length >= self.max_chunk_chars and line.strip() == "":
                chunk_text = "\n".join(current_buffer).strip()
                if chunk_text:
                    chunks.extend(
                        self._split_large_text(
                            chunk_text,
                            norm_path,
                            filename,
                            current_heading,
                            chunk_start_line,
                            idx,
                        )
                    )
                current_buffer = []
                chunk_start_line = idx + 1

        # 结尾剩余部分
        if current_buffer:
            chunk_text = "\n".join(current_buffer).strip()
            if chunk_text:
                chunks.extend(
                    self._split_large_text(
                        chunk_text,
                        norm_path,
                        filename,
                        current_heading,
                        chunk_start_line,
                        len(lines),
                    )
                )

        # 重新规整 chunk_id
        for i, c in enumerate(chunks):
            c.chunk_id = i
            c.embed_text = f"文档: {c.filename} | 章节: {c.heading}\n{c.content}"

        return chunks

    def _split_large_text(
        self,
        text: str,
        filepath: str,
        filename: str,
        heading: str,
        start_line: int,
        end_line: int,
    ) -> List[DocumentChunk]:
        if len(text) <= self.max_chunk_chars:
            return [
                DocumentChunk(
                    chunk_id=0,
                    filepath=filepath,
                    filename=filename,
                    heading=heading,
                    start_line=start_line,
                    end_line=end_line,
                    content=text,
                )
            ]

        # 长文本双换行或按句拆分
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        result = []
        buf = []
        buf_len = 0
        approx_line = start_line

        for p in paras:
            p_len = len(p)
            if buf_len + p_len > self.max_chunk_chars and buf:
                chunk_body = "\n\n".join(buf)
                lines_in_chunk = chunk_body.count("\n")
                result.append(
                    DocumentChunk(
                        chunk_id=0,
                        filepath=filepath,
                        filename=filename,
                        heading=heading,
                        start_line=approx_line,
                        end_line=min(approx_line + lines_in_chunk, end_line),
                        content=chunk_body,
                    )
                )
                approx_line += lines_in_chunk + 1
                buf = [p]
                buf_len = p_len
            else:
                buf.append(p)
                buf_len += p_len + 2

        if buf:
            chunk_body = "\n\n".join(buf)
            result.append(
                DocumentChunk(
                    chunk_id=0,
                    filepath=filepath,
                    filename=filename,
                    heading=heading,
                    start_line=approx_line,
                    end_line=end_line,
                    content=chunk_body,
                )
            )

        return result