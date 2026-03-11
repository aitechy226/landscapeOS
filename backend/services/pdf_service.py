"""
Quote PDF generation — stub implementation.
Replace with a real implementation (e.g. reportlab, weasyprint, or a template engine).
Raises ValueError for invalid input; other errors propagate so the API can return 500.
"""
from __future__ import annotations

import structlog
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from models.models import Quote

log = structlog.get_logger()

MAX_PDF_LINES = 24
MAX_LINE_BYTES = 200


def _safe_str(value: Any, max_len: int = 80) -> str:
    """Coerce value to string for PDF text; truncate to max_len."""
    if value is None:
        return ""
    s = str(value).strip()
    return s[:max_len] if len(s) > max_len else s


def _safe_amount(value: Any) -> str:
    """Format a numeric amount for display; avoid breaking on Decimal/float/None."""
    if value is None:
        return "0"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0"


def _minimal_pdf(title: str, body_lines: list[str]) -> bytes:
    """
    Build a minimal valid PDF (no external deps) with title and text lines.
    For production, replace with reportlab/weasyprint or HTML-to-PDF.
    """
    title = _safe_str(title, 100) or "Quote"
    safe_lines = []
    for line in (body_lines or [])[:MAX_PDF_LINES]:
        safe_lines.append(_safe_str(line, 120))

    raw_lines = [title] + safe_lines
    text_ops = b""
    y = 750
    for line in raw_lines:
        raw = line.encode("utf-8", errors="replace")
        if len(raw) > MAX_LINE_BYTES:
            raw = raw[:MAX_LINE_BYTES] + b"..."
        raw = raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        text_ops += b"BT /F1 12 Tf 72 " + str(y).encode() + b" Td (" + raw + b") Tj ET\n"
        y -= 18

    catalog = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    pages = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    media_box = b"[0 0 612 792]"
    page = (
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox " + media_box +
        b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    )
    stream = b"4 0 obj\n<< /Length " + str(len(text_ops)).encode() + b" >>\nstream\n" + text_ops + b"\nendstream\nendobj\n"
    font = b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"

    parts = [catalog, pages, page, stream, font]
    header = b"%PDF-1.4\n\n"
    body = b"".join(parts)
    offsets = []
    pos = len(header)
    for p in parts:
        offsets.append(pos)
        pos += len(p)

    xref_lines = [b"0000000000 65535 f "]
    for o in offsets:
        xref_lines.append(("%010d 00000 n " % o).encode())
    xref = b"xref\n0 6\n" + b"\n".join(xref_lines) + b"\n"
    trailer = b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(pos).encode() + b"\n%%EOF\n"
    return header + body + xref + trailer


def _quote_to_lines(quote: "Quote") -> list[str]:
    """Extract key fields from the quote for the stub PDF. Tolerates missing or bad data."""
    lines = []
    try:
        num = getattr(quote, "quote_number", None)
        lines.append(f"Quote #{_safe_str(num, 50) or 'N/A'}")
    except Exception:
        lines.append("Quote #N/A")
    lines.append("")

    try:
        desc = getattr(quote, "description", None)
        if desc:
            lines.append(_safe_str(desc, 80))
    except Exception:
        pass

    try:
        client = getattr(quote, "client", None)
        if client is not None and hasattr(client, "first_name"):
            first = _safe_str(getattr(client, "first_name", ""), 50)
            last = _safe_str(getattr(client, "last_name", ""), 50)
            name = f"{first} {last}".strip()
            if name:
                lines.append(f"Client: {name}")
    except Exception:
        pass

    items = getattr(quote, "ai_line_items", None) or []
    if isinstance(items, list) and items:
        lines.append("")
        lines.append("Line items:")
        for item in items[:10]:
            if isinstance(item, dict):
                desc = _safe_str(item.get("description"), 40) or "—"
                total = _safe_amount(item.get("total"))
                lines.append(f"  • {desc}: ${total}")

    lines.append("")
    lines.append(f"Subtotal: ${_safe_amount(getattr(quote, 'subtotal', None))}")
    lines.append(f"Tax: ${_safe_amount(getattr(quote, 'tax_amount', None))}")
    lines.append(f"Discount: ${_safe_amount(getattr(quote, 'discount_amount', None))}")
    lines.append(f"Total: ${_safe_amount(getattr(quote, 'total', None))}")
    lines.append("")
    lines.append("(This is a stub PDF. Implement full layout in pdf_service.)")
    return lines


async def generate_quote_pdf(quote: "Quote") -> bytes:
    """
    Generate a PDF for the given quote (stub: minimal valid PDF with quote details).

    Replace this with a real implementation using reportlab, weasyprint,
    or an HTML template + PDF library. The quote ORM has: id, quote_number,
    client (relationship), description, ai_line_items, subtotal, tax_amount,
    discount_amount, total, valid_until, internal_notes, etc.

    Raises ValueError if quote is None or invalid; other errors propagate.
    """
    if quote is None:
        log.warning("pdf.generate_quote_pdf_called_with_none")
        raise ValueError("Quote is required to generate PDF.")

    try:
        quote_number = getattr(quote, "quote_number", None)
        title = f"Quote {_safe_str(quote_number, 50) or 'N/A'}"
    except Exception as e:
        log.warning("pdf.quote_title_failed", error=str(e))
        title = "Quote"

    try:
        body_lines = _quote_to_lines(quote)
    except Exception as e:
        log.exception("pdf.quote_to_lines_failed", error=str(e), quote_id=str(getattr(quote, "id", "")))
        raise ValueError("Could not read quote data for PDF.") from e

    try:
        pdf_bytes = _minimal_pdf(title, body_lines)
    except Exception as e:
        log.exception("pdf.build_failed", error=str(e), quote_id=str(getattr(quote, "id", "")))
        raise ValueError("Could not build PDF.") from e

    if not pdf_bytes or len(pdf_bytes) == 0:
        log.warning("pdf.empty_output", quote_id=str(getattr(quote, "id", "")))
        raise ValueError("PDF generation produced no output.")

    log.info(
        "pdf.quote_generated",
        quote_id=str(getattr(quote, "id", "")),
        quote_number=getattr(quote, "quote_number", ""),
        size_bytes=len(pdf_bytes),
    )
    return pdf_bytes
