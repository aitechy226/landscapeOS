"""
Send quote by email via Resend.
Requires RESEND_API_KEY and EMAIL_FROM in config. Raises ValueError if not configured or recipient missing.
"""
from __future__ import annotations

import html
import structlog
from typing import TYPE_CHECKING, Any

from config import settings

if TYPE_CHECKING:
    from models.models import Quote

log = structlog.get_logger()


def _safe_str(value: Any, max_len: int = 500) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return s[:max_len] if len(s) > max_len else s


def _build_quote_email_html(quote: "Quote", custom_message: str) -> str:
    """Build a simple HTML body for the quote email."""
    desc = _safe_str(quote.description, 1000) or "Landscaping quote"
    lines = []
    for item in (quote.ai_line_items or [])[:50]:
        if not isinstance(item, dict):
            continue
        desc_item = _safe_str(item.get("description"), 200) or "Item"
        qty = item.get("quantity", 0)
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            qty = 0
        up = item.get("unit_price", 0)
        try:
            up = float(up)
        except (TypeError, ValueError):
            up = 0
        total = item.get("total")
        if total is None:
            total = round(qty * up, 2)
        else:
            try:
                total = float(total)
            except (TypeError, ValueError):
                total = 0
        lines.append(f"<tr><td>{html.escape(desc_item)}</td><td>{qty}</td><td>${up:.2f}</td><td>${total:.2f}</td></tr>")

    subtotal = float(quote.subtotal or 0)
    tax = float(quote.tax_amount or 0)
    discount = float(quote.discount_amount or 0)
    total = float(quote.total or 0)
    table_rows = "".join(lines) if lines else "<tr><td colspan='4'>No line items.</td></tr>"

    body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Quote {html.escape(quote.quote_number or "")}</title></head>
<body style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 16px;">
  <h2 style="color: #166534;">Quote {html.escape(quote.quote_number or "")}</h2>
  <p style="white-space: pre-wrap;">{html.escape(desc)}</p>
  {f'<p>{html.escape(_safe_str(custom_message, 1000))}</p>' if custom_message else ''}
  <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
    <thead>
      <tr style="border-bottom: 2px solid #166534;">
        <th style="text-align: left; padding: 8px;">Description</th>
        <th style="text-align: right; padding: 8px;">Qty</th>
        <th style="text-align: right; padding: 8px;">Unit price</th>
        <th style="text-align: right; padding: 8px;">Total</th>
      </tr>
    </thead>
    <tbody>
      {table_rows}
    </tbody>
  </table>
  <p style="text-align: right;">
    Subtotal: ${subtotal:.2f} &middot; Tax: ${tax:.2f} &middot; Discount: ${discount:.2f}<br>
    <strong>Total: ${total:.2f}</strong>
  </p>
  <p style="color: #666; font-size: 12px;">This quote was sent from LandscapeOS.</p>
</body>
</html>
"""
    return body.strip()


async def send_quote_email(quote: "Quote", message: str = "") -> None:
    """
    Send the quote to the client's email via Resend.
    Quote must have client loaded (e.g. from get_with_client). Raises ValueError if
    RESEND_API_KEY is missing, or client has no email.
    """
    if not quote:
        raise ValueError("Quote is required to send email.")
    api_key = (getattr(settings, "RESEND_API_KEY", None) or "").strip()
    if not api_key:
        raise ValueError(
            "Email is not configured. Add RESEND_API_KEY to your .env (get a key at resend.com)."
        )
    client = getattr(quote, "client", None)
    if not client:
        raise ValueError("Quote has no client. Cannot send email.")
    to_email = (getattr(client, "email", None) or "").strip()
    if not to_email or "@" not in to_email:
        raise ValueError("Client has no valid email address. Add an email to the client to send the quote.")

    from_email = (getattr(settings, "EMAIL_FROM", None) or "noreply@landscapeos.com").strip()
    from_name = (getattr(settings, "EMAIL_FROM_NAME", None) or "LandscapeOS").strip()
    from_header = f"{from_name} <{from_email}>" if from_name else from_email

    subject = f"Your quote {quote.quote_number or ''} from {from_name}"
    html_body = _build_quote_email_html(quote, message or "")

    import resend
    resend.api_key = api_key
    params = {
        "from": from_header,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }
    try:
        resend.Emails.send(params)
    except Exception as e:
        log.warning("email.send_failed", quote_id=str(getattr(quote, "id", "")), to=to_email, error=str(e))
        raise ValueError(f"Failed to send email: {str(e)}") from e
    log.info("email.quote_sent", quote_id=str(getattr(quote, "id", "")), to=to_email)
