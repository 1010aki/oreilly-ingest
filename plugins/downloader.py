"""Download orchestration plugin."""

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from plugins.base import Plugin
from plugins.chunking import ChunkConfig


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

    FORMAT_ALIASES = {
        "md": "markdown",
        "txt": "plaintext",
    }

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
            "markdown-chapters": "Separate Markdown file per chapter",
            "pdf": "Single PDF file",
            "pdf-chapters": "Separate PDF per chapter",
            "plaintext": "Plain text (alias: txt)",
            "plaintext-chapters": "Separate text file per chapter",
            "json": "Structured JSON export",
            "jsonl": "JSON Lines format (includes json)",
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
    # 修正ポイント1: 階層構造（Children）を持つチャプターを一列に並べ直す関数
    # =========================================================================
    def _flatten_chapters(self, chapters: list[dict]) -> list[dict]:
        """Recursively flatten the chapters list."""
        flat_list = []
        for ch in chapters:
            # まず自分自身を追加
            flat_list.append(ch)
            # 子供がいれば、再帰的に追加
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

        # Get plugins
        book_plugin = self.kernel["book"]
        chapters_plugin = self.kernel["chapters"]
        assets_plugin = self.kernel["assets"]
        html_processor = self.kernel["html_processor"]
        output_plugin = self.kernel["output"]

        # Phase 1: Fetch metadata
        report("starting", 0)
        report("fetching_metadata", 5)
        book_info = book_plugin.fetch(book_id)

        # Phase 2: Fetch chapters list
        report("fetching_chapters", 10)
        raw_chapters = chapters_plugin.fetch_list(book_id)  # 元のリスト
        
        # =========================================================================
        # 修正ポイント1適用: リストをフラット化して全ページを取得
        # =========================================================================
        all_chapters = self._flatten_chapters(raw_chapters)
        
        toc = chapters_plugin.fetch_toc(book_id)

        if selected_chapters is not None:
            selected_set = set(selected_chapters)
            chapters = [ch for i, ch in enumerate(all_chapters) if i in selected_set]
        else:
            chapters = all_chapters

        # Create output directory
        book_dir = output_plugin.create_book_dir(
            output_dir=output_dir,
            book_id=book_id,
            title=book_info.get("title", ""),
            authors=book_info.get("authors"),
        )
        oebps = output_plugin.get_oebps_dir(book_dir)

        # Phase 3: Download cover
        if not skip_images:
            report("downloading_cover", 12)
            cover_url = book_info.get("cover_url")
            if cover_url:
                images_dir = output_plugin.get_images_dir(book_dir)
                images_dir.mkdir(parents=True, exist_ok=True)
                assets_plugin.download_image(cover_url, images_dir / "cover.jpg")

        # Phase 4: Process chapters
        all_css_urls = set()
        all_image_urls = set()
        chapters_data = []
        total_chapters = len(chapters)
        chapter_times = []
        chapter_start_time = time.time()

        for i, ch in enumerate(chapters):
            if check_cancel():
                self._cleanup_on_cancel(book_dir)
                raise Exception("Download cancelled by user")

            chapter_pct = 15 + int((i / total_chapters) * 65) if total_chapters > 0 else 15

            report("processing_chapters", chapter_pct, current_chapter=i + 1, total_chapters=total_chapters,
                   chapter_title=ch.get("title", ""))

            # Fetch and process chapter content
            raw_html = chapters_plugin.fetch_content(ch["content_url"])
            processed, images = html_processor.process(raw_html, book_id, skip_images=skip_images)

            # =========================================================================
            # 修正ポイント2: 日本語フォント強制適用（EPUBの豆腐対策）
            # =========================================================================
            # HTMLの先頭に強力なスタイルタグを注入します
            font_style = (
                '<style>'
                'body, p, div, span, h1, h2, h3, h4, h5, h6, td, th, li, a '
                '{ font-family: "Noto Sans CJK JP", "Noto Sans", sans-serif !important; }'
                '</style>\n'
            )
            processed = font_style + processed

            all_css_urls.update(ch["stylesheets"])
            for img_url in ch["images"]:
                all_image_urls.add(img_url)
            for img_url in images:
                if img_url.startswith("http") or img_url.startswith("/"):
                    all_image_urls.add(img_url)

            css_refs = [f"Styles/Style{j:02d}.css" for j in range(len(all_css_urls))]
            xhtml = html_processor.wrap_xhtml(processed, css_refs, ch["title"])

            filename = ch["filename"].replace(".html", ".xhtml")
            file_path = oebps / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(xhtml)

            chapters_data.append((ch["filename"], ch["title"], processed))

            chapter_time = time.time() - chapter_start_time
            chapter_times.append(chapter_time)
            chapter_start_time = time.time()

            if chapter_times:
                avg_time = sum(chapter_times[-5:]) / len(chapter_times[-5:])
                remaining = total_chapters - (i + 1)
                eta_seconds = int(avg_time * remaining)
                report("processing_chapters", chapter_pct, eta_seconds=eta_seconds,
                       current_chapter=i + 1, total_chapters=total_chapters, chapter_title=ch.get("title", ""))

        # Phase 5: Download assets
        report("downloading_assets", 80, eta_seconds=None)

        image_list = []
        for img_url in all_image_urls:
            if img_url.startswith("/"):
                img_url = f"https://learning.oreilly.com{img_url}"
            image_list.append(img_url)

        css_list = list(all_css_urls)
        total_assets = len(css_list) + len(image_list)

        css_width = len(str(len(css_list)))

        def css_progress(completed: int, total: int):
            if total_assets > 0:
                pct = 80 + int((completed / total_assets) * 10)
                report("downloading_assets", pct, f"{pct:2d}% - Downloading CSS ({completed:>{css_width}}/{len(css_list)})")

        assets_plugin.download_all_css(css_list, oebps, progress_callback=css_progress)

        if not skip_images:
            img_width = len(str(len(image_list)))
            def image_progress(completed: int, total: int):
                if total_assets > 0:
                    pct = 80 + int(((len(css_list) + completed) / total_assets) * 10)
                    report("downloading_assets", pct, f"{pct:2d}% - Downloading images ({completed:>{img_width}}/{len(image_list)})")

            assets_plugin.download_all_images(image_list, oebps, progress_callback=image_progress)

        # Phase 6: Generate output formats
        result = DownloadResult(
            book_id=book_id,
            title=book_info.get("title", ""),
            output_dir=book_dir,
            chapters_count=len(chapters_data),
        )

        if "epub" in formats:
            report("generating_epub", 90)
            epub_plugin = self.kernel["epub"]
            epub_path = epub_plugin.generate(
                book_info=book_info,
                chapters=chapters,  # ここでフラット化されたchaptersが渡されるのでTOCも正常化されます
                toc=toc,
                output_dir=book_dir,
                css_files=css_list,
                cover_image="cover.jpg",
            )
            result.files["epub"] = str(epub_path)

        if "markdown" in formats or "md" in formats or "markdown-chapters" in formats:
            report("generating_markdown", 92)
            md_plugin = self.kernel["markdown"]
            md_plugin.generate_book(book_info, chapters_data, book_dir)
            result.files["markdown"] = str(book_dir / "Markdown")

        if "pdf" in formats or "all" in formats or "pdf-chapters" in formats:
            pdf_plugin = self.kernel["pdf"]
            if "pdf-chapters" in formats:
                report("generating_pdf_chapters", 95)
                pdf_paths = pdf_plugin.generate_chapters(book_info=book_info, chapters=chapters, output_dir=book_dir, css_files=css_list)
                result.files["pdf"] = [str(p) for p in pdf_paths]
            else:
                report("generating_pdf", 95)
                pdf_path = pdf_plugin.generate(book_info=book_info, chapters=chapters, toc=toc, output_dir=book_dir, css_files=css_list, cover_image="cover.jpg")
                result.files["pdf"] = str(pdf_path)

        if "plaintext" in formats or "txt" in formats or "plaintext-chapters" in formats:
            report("generating_plaintext", 96)
            plaintext_plugin = self.kernel["plaintext"]
            single_file = "plaintext-chapters" not in formats
            txt_path = plaintext_plugin.generate(book_dir=book_dir, book_metadata=book_info, chapters_data=chapters_data, single_file=single_file)
            result.files["plaintext"] = str(txt_path)

        if "json" in formats:
            report("generating_json", 97)
            json_plugin = self.kernel["json_export"]
            json_path = json_plugin.generate(book_dir=book_dir, book_metadata=book_info, chapters_data=chapters_data, include_jsonl="jsonl" in formats)
            result.files["json"] = str(json_path)

        if "chunks" in formats:
            report("generating_chunks", 98)
            chunking_plugin = self.kernel["chunking"]
            chunks_path = chunking_plugin.generate(book_dir=book_dir, book_metadata=book_info, chapters_data=chapters_data, config=chunk_config)
            result.files["chunks"] = str(chunks_path)

        report("completed", 100)
        return result

    def _cleanup_on_cancel(self, book_dir: Path):
        if book_dir.exists():
            shutil.rmtree(book_dir)