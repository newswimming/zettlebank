# readers.py
"""
Text extraction for screenplay files.

Extraction order for PDFs:
  1. PyPDF2          — fast; works on text-layer PDFs
  2. PyMuPDF (fitz)  — broader format support; also text-layer only
  3. PyMuPDF OCR     — full Tesseract OCR for image-only / scanned PDFs
                       requires Tesseract installed:
                         Windows: https://github.com/UB-Mannheim/tesseract/wiki
                         macOS:   brew install tesseract
                         Linux:   sudo apt install tesseract-ocr
"""
from __future__ import annotations

import os
import pathlib
from typing import Optional

from PyPDF2 import PdfReader


# Common Windows Tesseract install paths (searched if not already on PATH)
_TESSERACT_WIN_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def _set_tesseract_path() -> bool:
    """Point PyMuPDF at Tesseract if it is installed but not on PATH.

    Returns True if Tesseract is usable, False otherwise.
    """
    try:
        import fitz  # noqa: F401 — just verify import
    except ImportError:
        return False

    # Check PATH first
    import shutil
    if shutil.which("tesseract"):
        return True

    # Try well-known Windows locations
    for candidate in _TESSERACT_WIN_PATHS:
        if os.path.isfile(candidate):
            os.environ["PATH"] = os.path.dirname(candidate) + os.pathsep + os.environ.get("PATH", "")
            return True

    return False


def _extract_with_pymupdf(path: str) -> str:
    """Extract text via PyMuPDF. Returns empty string if not installed."""
    try:
        import fitz
    except ImportError:
        return ""

    doc = fitz.open(path)
    parts = [page.get_text() for page in doc]
    doc.close()
    return "\n\n".join(parts)


def _extract_with_ocr(path: str) -> Optional[str]:
    """Extract text via PyMuPDF + Tesseract OCR. Returns None if unavailable."""
    try:
        import fitz
    except ImportError:
        return None

    if not _set_tesseract_path():
        return None

    doc = fitz.open(path)
    parts: list[str] = []
    for page in doc:
        try:
            tp = page.get_textpage_ocr(flags=3, language="eng", dpi=300, full=True)
            parts.append(page.get_text(textpage=tp))
        except Exception:
            parts.append(page.get_text())  # fallback to non-OCR for this page
    doc.close()
    return "\n\n".join(parts)


def read_pdf_text(path: str) -> str:
    # 1. PyPDF2 (text-layer)
    try:
        reader = PdfReader(path)
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            return text
    except Exception:
        pass

    # 2. PyMuPDF (text-layer, broader support)
    text = _extract_with_pymupdf(path)
    if text.strip():
        return text

    # 3. PyMuPDF + Tesseract OCR (scanned / image-only PDFs)
    ocr_text = _extract_with_ocr(path)
    if ocr_text and ocr_text.strip():
        print(f"[readers] OCR used for {path}")
        return ocr_text

    # 4. Nothing worked
    has_tesseract = _set_tesseract_path()
    if not has_tesseract:
        print(
            f"[pipeline] WARNING: No extractable text from {pathlib.Path(path).name}. "
            "This file may be scanned (image-only).\n"
            "  Install Tesseract OCR to enable automatic OCR:\n"
            "    Windows: https://github.com/UB-Mannheim/tesseract/wiki\n"
            "    macOS:   brew install tesseract\n"
            "    Linux:   sudo apt install tesseract-ocr\n"
            "  Then re-run the pipeline."
        )
    else:
        print(
            f"[pipeline] WARNING: No extractable text from {pathlib.Path(path).name}. "
            "Provide a .txt or manually OCR'd version."
        )
    return ""


def read_text(path: str) -> str:
    p = pathlib.Path(path)
    if p.suffix.lower() == ".pdf":
        return read_pdf_text(path)
    else:
        return p.read_text(encoding="utf-8")
