"""Download orchestration plugin."""

import shutil
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from plugins.base import Plugin
from plugins.chunking import ChunkConfig

# ロガーの設定
logger = logging.getLogger(__name__)

@dataclass
class DownloadProgress:
    """Progress state for download operations."""
    status: str
    percentage: int = 0
    message: str = ""
    eta_seconds: int | None = None
    current_chapter: int = 0
    total_chapters: int = 0
    chapter_title: str = ""
    book_id: str = ""

@dataclass
class DownloadResult:
    """Result of a completed download."""
    book_id: str
    title: str
    output_dir: Path
    files: dict = field(default_factory=dict)
    chapters_count: int = 0

class DownloaderPlugin(Plugin):
    """Orchestrates the complete book download workflow."""

    SUPPORTED_FORMATS = frozenset([
        "epub", "markdown", "markdown-chapters", "pdf", "pdf-chapters",
        "plaintext", "plaintext-chapters", "json", "jsonl", "chunks",
    ])
    FORMAT_ALIASES = {"md": "markdown", "txt": "plaintext"}
    BOOK_ONLY_FORMATS = frozenset(["epub", "chunks"])

    @classmethod
    def parse_formats(cls, format_input: str | list[str]) -> list[str]:
        if isinstance(format_input, list):
            raw_formats = format_input
        else:
            if format_input == "all":
                return ["epub", "markdown", "pdf", "plaintext", "json", "chunks"]
            raw_formats = [f.strip().lower() for f in format_input.split(",") if f.strip()]
        
        formats = []
        seen = set()
        for fmt in raw_formats:
            canonical = cls.FORMAT_ALIASES.get(fmt, fmt)
            if canonical == "jsonl" and "json" not in seen:
                formats.append("json")
                seen.add("json")
            if canonical == "jsonl":
                formats.append("jsonl")
                seen.add("jsonl")
                continue
            if canonical not in cls.SUPPORTED_FORMATS or canonical in seen:
                continue
            formats.append(canonical)
            seen.add(canonical)
        return formats if formats else ["epub"]

    @classmethod
    def get_format_help(cls) -> dict[str, str]:
        return {
            "epub": "Standard EPUB format (default)",
            "markdown": "Markdown files (alias: md)",
            "pdf": "Single PDF file",
            "plaintext": "Plain text (alias: txt)",
            "json": "Structured JSON export",
            "chunks": "Chunked content for LLM processing",
        }

    @classmethod
    def supports_chapter_selection(cls, fmt: str) -> bool:
        canonical = cls.FORMAT_ALIASES.get(fmt, fmt)
        return canonical not in cls.BOOK_ONLY_FORMATS

    @classmethod
    def get_formats_info(cls) -> dict:
        return {
            "formats": sorted(cls.SUPPORTED_FORMATS),
            "aliases": cls.FORMAT_ALIASES,
            "book_only": sorted(cls.BOOK_ONLY_FORMATS),
            "descriptions": cls.get_format_help(),
        }

    # =========================================================================
    # 修正ポイント: チャプターの階層構造（Children）をすべて展開する関数
    # =========================================================================
    def _flatten_chapters(self, chapters: list[dict]) -> list[dict]:
        """Recursively flatten the chapters list."""
        flat_list = []
        for ch in chapters:
            # 自分自身を追加
            flat_list.append(ch)
            # 子供がいれば、再帰的に取り出して追加
            if "children" in ch and ch["children"]:
                flat_list.extend(self._flatten_chapters(ch["children"]))
        return flat_list

    def download(
        self,
        book_id: str,
        output_dir: Path,
        formats: list[str] | None = None,
        selected_chapters: list[int] | None = None,
        skip_images: bool = False,
        chunk_config: ChunkConfig | None = None,
        progress_callback: Callable[[DownloadProgress], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> DownloadResult:
        if formats is None:
            formats = ["epub"]

        def report(status: str, percentage: int = 0, message: str = "", eta_seconds: int | None = None,
                   current_chapter: int = 0, total_chapters: int = 0, chapter_title: str = ""):
            if progress_callback:
                progress_callback(DownloadProgress(
                    status=status, percentage=percentage, message=message, eta_seconds=eta_seconds,
                    current_chapter=current_chapter, total_chapters=total_chapters,
                    chapter_title=chapter_title, book_id=book_id))

        def check_cancel():
            if cancel_check and cancel_check():
                return True
            return False

        book_plugin = self.kernel["book"]
        chapters_plugin = self.kernel["chapters"]
        assets_plugin = self.kernel["assets"]
        html_processor = self.kernel["html_processor"]
        output_plugin = self.kernel["output"]

        # Phase 1: Fetch metadata
        report("starting", 0)
        book_info = book_plugin.fetch(book_id)

        # Phase 2: Fetch chapters list
        report("fetching_chapters", 10)
        
        # ---