"""
extract_pdf.py
--------------
Stage 1 of the Personal Finance Data Architect pipeline.

Responsibility:
  - Accept a PDF or plain-text statement from uploads/
  - Try three extraction strategies in order of quality
  - Write clean raw text to data/raw_text.txt
  - Report extraction stats so the orchestrator knows what to pass to the Extraction Agent

Usage (called by orchestrator or directly):
  python scripts/extract_pdf.py --file uploads/statement_june.pdf
  python scripts/extract_pdf.py --file uploads/statement_june.txt   # plain text passthrough

Output:
  data/raw_text.txt     — clean text ready for Extraction Agent
  data/extract_log.json — stats about what was extracted and which strategy succeeded
"""

import argparse
import json
import os
import sys
import re
from datetime import datetime
from pathlib import Path


# ── Path constants ─────────────────────────────────────────────────────────────

ROOT        = Path(__file__).resolve().parent.parent   # finance-architect/
UPLOADS_DIR = ROOT / "uploads"
DATA_DIR    = ROOT / "data"
OUT_TEXT    = DATA_DIR / "raw_text.txt"
OUT_LOG     = DATA_DIR / "extract_log.json"


# ── Strategy 1: pdfplumber (best for columnar bank statements) ─────────────────

def extract_with_pdfplumber(pdf_path: Path) -> tuple[str, dict]:
    """
    Uses pdfplumber which understands layout and table structure.
    Best choice for modern digital bank statement PDFs with clear columns.
    Returns (extracted_text, stats).
    """
    import pdfplumber

    pages_text = []
    stats = {"strategy": "pdfplumber", "pages": 0, "tables_found": 0, "chars": 0}

    with pdfplumber.open(pdf_path) as pdf:
        stats["pages"] = len(pdf.pages)

        for page_num, page in enumerate(pdf.pages, start=1):

            page_lines = []

            # Try table extraction first — most bank statements are tabular
            tables = page.extract_tables()
            if tables:
                stats["tables_found"] += len(tables)
                for table in tables:
                    for row in table:
                        # Join non-None cells with a tab separator so column
                        # alignment is preserved for the Extraction Agent
                        clean_row = "\t".join(
                            cell.strip().replace("\n", " ") if cell else ""
                            for cell in row
                        )
                        if clean_row.strip():
                            page_lines.append(clean_row)
            else:
                # Fall back to plain text extraction for this page
                text = page.extract_text(x_tolerance=3, y_tolerance=3)
                if text:
                    page_lines.append(text)

            if page_lines:
                pages_text.append(f"--- PAGE {page_num} ---\n" + "\n".join(page_lines))

    full_text = "\n\n".join(pages_text)
    stats["chars"] = len(full_text)
    return full_text, stats


# ── Strategy 2: pypdf (fallback for encrypted or non-tabular PDFs) ─────────────

def extract_with_pypdf(pdf_path: Path) -> tuple[str, dict]:
    """
    Uses pypdf as a fallback. Less layout-aware but handles more PDF types
    including some that pdfplumber struggles with.
    Returns (extracted_text, stats).
    """
    from pypdf import PdfReader

    stats = {"strategy": "pypdf", "pages": 0, "chars": 0}
    pages_text = []

    reader = PdfReader(pdf_path)
    stats["pages"] = len(reader.pages)

    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        if text and text.strip():
            pages_text.append(f"--- PAGE {page_num} ---\n{text.strip()}")

    full_text = "\n\n".join(pages_text)
    stats["chars"] = len(full_text)
    return full_text, stats


# ── Strategy 3: OCR via pytesseract (for scanned / image-based PDFs) ──────────

def extract_with_ocr(pdf_path: Path) -> tuple[str, dict]:
    """
    Last resort for scanned PDFs where no text layer exists.
    Requires: pytesseract + poppler (pdf2image).
    Returns (extracted_text, stats).
    """
    import pytesseract
    from pdf2image import convert_from_path

    stats = {"strategy": "ocr_tesseract", "pages": 0, "chars": 0}
    pages_text = []

    images = convert_from_path(pdf_path, dpi=300)
    stats["pages"] = len(images)

    for page_num, image in enumerate(images, start=1):
        # Use psm 6 (uniform block of text) — works well for statement layouts
        config = "--psm 6 -l eng"
        text = pytesseract.image_to_string(image, config=config)
        if text.strip():
            pages_text.append(f"--- PAGE {page_num} (OCR) ---\n{text.strip()}")

    full_text = "\n\n".join(pages_text)
    stats["chars"] = len(full_text)
    return full_text, stats


# ── Plain text passthrough (for .txt / .csv inputs) ───────────────────────────

def extract_plain_text(file_path: Path) -> tuple[str, dict]:
    """
    For non-PDF inputs — just read and return the text as-is.
    """
    text = file_path.read_text(encoding="utf-8", errors="replace")
    stats = {
        "strategy": "plain_text_passthrough",
        "pages": 1,
        "chars": len(text),
    }
    return text, stats


# ── Quality check: is the extracted text good enough? ─────────────────────────

def is_extraction_usable(text: str) -> bool:
    """
    Heuristic checks to decide if extracted text is worth passing to the agent.

    A usable extraction should:
    - Have at least 200 characters
    - Contain at least one date-like pattern (DD/MM or YYYY-MM etc.)
    - Contain at least one number that looks like a currency amount
    """
    if len(text.strip()) < 200:
        return False

    date_pattern    = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\d{4}-\d{2}-\d{2}")
    amount_pattern  = re.compile(r"\b\d{1,3}(?:,\d{2,3})*(?:\.\d{2})?\b")

    has_dates   = bool(date_pattern.search(text))
    has_amounts = bool(amount_pattern.search(text))

    return has_dates and has_amounts


# ── Post-processing: clean extracted text ─────────────────────────────────────

def clean_text(text: str) -> str:
    """
    Light cleanup before writing to raw_text.txt.
    Does NOT remove content — only normalises whitespace.
    The Extraction Agent does the semantic filtering.
    """
    lines = text.splitlines()
    cleaned = []

    for line in lines:
        # Collapse internal whitespace runs to a single space
        line = re.sub(r"[ \t]+", " ", line)
        # Strip leading/trailing whitespace per line
        line = line.strip()
        cleaned.append(line)

    # Collapse more than 2 consecutive blank lines into 2
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned))
    return result.strip()


# ── Main orchestration ─────────────────────────────────────────────────────────

def run(file_arg: str) -> None:

    file_path = Path(file_arg)

    # Resolve relative paths against project root
    if not file_path.is_absolute():
        file_path = ROOT / file_path

    # ── Validate input ────────────────────────────────────────────────────────
    if not file_path.exists():
        print(f"[ERROR] File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    suffix = file_path.suffix.lower()
    if suffix not in {".pdf", ".txt", ".csv"}:
        print(f"[ERROR] Unsupported file type: {suffix}. Expected .pdf, .txt, or .csv", file=sys.stderr)
        sys.exit(1)

    # Ensure output directory exists
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n[extract_pdf.py] Processing: {file_path.name}")
    print(f"[extract_pdf.py] File size : {file_path.stat().st_size / 1024:.1f} KB")

    # ── Run extraction ────────────────────────────────────────────────────────
    raw_text = ""
    final_stats = {}
    strategy_used = None

    if suffix in {".txt", ".csv"}:
        raw_text, final_stats = extract_plain_text(file_path)
        strategy_used = "plain_text_passthrough"
        print(f"[extract_pdf.py] Strategy  : plain text passthrough")

    else:
        # Try strategies in priority order
        strategies = [
            ("pdfplumber", extract_with_pdfplumber),
            ("pypdf",      extract_with_pypdf),
            ("ocr",        extract_with_ocr),
        ]

        for name, fn in strategies:
            print(f"[extract_pdf.py] Trying    : {name} ...", end=" ", flush=True)
            try:
                text, stats = fn(file_path)
                if is_extraction_usable(text):
                    raw_text     = text
                    final_stats  = stats
                    strategy_used = name
                    print(f"OK  ({stats['chars']} chars, {stats['pages']} pages)")
                    break
                else:
                    print(f"POOR QUALITY (chars={len(text)}) — trying next strategy")
            except ImportError as e:
                print(f"SKIPPED (library not installed: {e})")
            except Exception as e:
                print(f"FAILED ({e}) — trying next strategy")

    if not raw_text:
        print("\n[ERROR] All extraction strategies failed or produced unusable output.", file=sys.stderr)
        print("[ERROR] Options:", file=sys.stderr)
        print("  1. Check the PDF is not password-protected", file=sys.stderr)
        print("  2. Install OCR: pip install pytesseract pdf2image", file=sys.stderr)
        print("  3. Export the statement as plain text and use .txt input", file=sys.stderr)
        sys.exit(1)

    # ── Clean and write output ────────────────────────────────────────────────
    clean = clean_text(raw_text)
    OUT_TEXT.write_text(clean, encoding="utf-8")
    print(f"[extract_pdf.py] Written   : {OUT_TEXT.relative_to(ROOT)}")

    # ── Write extraction log ──────────────────────────────────────────────────
    log = {
        "timestamp"      : datetime.now().isoformat(),
        "source_file"    : str(file_path.name),
        "strategy_used"  : strategy_used,
        "pages_extracted": final_stats.get("pages", 1),
        "tables_found"   : final_stats.get("tables_found", 0),
        "chars_extracted": len(clean),
        "lines_extracted": clean.count("\n") + 1,
        "output_file"    : str(OUT_TEXT.relative_to(ROOT)),
        "quality_check"  : "passed",
    }
    OUT_LOG.write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"[extract_pdf.py] Log       : {OUT_LOG.relative_to(ROOT)}")

    # ── Summary for orchestrator ──────────────────────────────────────────────
    print(f"\n{'─' * 50}")
    print(f"  Extraction complete")
    print(f"  Strategy  : {strategy_used}")
    print(f"  Pages     : {log['pages_extracted']}")
    print(f"  Lines     : {log['lines_extracted']}")
    print(f"  Characters: {log['chars_extracted']}")
    print(f"{'─' * 50}")
    print(f"\n[READY] Pass data/raw_text.txt to the Extraction Agent.\n")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract raw text from a bank statement PDF or text file."
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the statement file (relative to project root or absolute). "
             "Example: uploads/statement_june.pdf"
    )
    args = parser.parse_args()
    run(args.file)