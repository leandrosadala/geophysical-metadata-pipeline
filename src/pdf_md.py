from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz
import pymupdf4llm


@dataclass(frozen=True)
class PageMD:
    page: int   # 1..N physical page index
    md: str


class PageMarkdownStore:
    """
    Lazy page -> markdown extractor with in-memory cache.
    Uses pymupdf4llm.to_markdown(doc, pages=[i]) (0-based indices).
    """

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)
        self._doc = fitz.open(str(self.pdf_path))
        self.total_pages = self._doc.page_count
        self._cache: dict[int, str] = {}  # key: 1-based page number

    def close(self) -> None:
        try:
            self._doc.close()
        except Exception:
            pass

    def get_page_md(self, page_number: int) -> PageMD:
        if page_number < 1 or page_number > self.total_pages:
            raise ValueError(f"page_number out of range: {page_number}")

        if page_number in self._cache:
            return PageMD(page=page_number, md=self._cache[page_number])

        i0 = page_number - 1  # 0-based for pymupdf4llm
        md = pymupdf4llm.to_markdown(self._doc, pages=[i0]) or ""
        self._cache[page_number] = md
        return PageMD(page=page_number, md=md)

    def get_pages_md(self, page_numbers: list[int]) -> list[PageMD]:
        # Keep order as provided; unique to avoid duplicate work
        out: list[PageMD] = []
        seen: set[int] = set()
        for p in page_numbers:
            if p in seen:
                continue
            seen.add(p)
            out.append(self.get_page_md(p))
        return out

    def get_range_md(self, start_page: int, end_page: int) -> list[PageMD]:
        if start_page > end_page:
            return []
        start_page = max(1, start_page)
        end_page = min(self.total_pages, end_page)
        return [self.get_page_md(p) for p in range(start_page, end_page + 1)]
