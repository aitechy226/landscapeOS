"""
Quote API — CRUD, AI generation, send/download.
All endpoints are tenant-scoped via QuoteRepo.
"""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
import structlog

from db.database import get_db
from models.models import User, Quote, QuoteStatus
from schemas.schemas import (
    CreateQuoteRequest,
    UpdateQuoteRequest,
    AIGenerateRequest,
    SendQuoteRequest,
    DeleteQuoteRequest,
    QuoteResponse,
    ClientResponse,
    PaginatedResponse,
)
from repositories.repositories import QuoteRepo, ClientRepo, TenantRepo
from middleware.security import require_permission, get_current_user, audit_log

log = structlog.get_logger()
router = APIRouter(prefix="/quotes", tags=["Quotes"])


def _user_error(message: str, status: int = 400):
    """Return 4xx with a consistent shape the frontend can show."""
    return HTTPException(status_code=status, detail={"message": message})


def _not_found(message: str = "Quote not found."):
    return HTTPException(status_code=404, detail={"message": message})


def _quote_to_response(quote: Quote, include_client: bool = False) -> QuoteResponse:
    """Build QuoteResponse from ORM; optionally include nested client as dict."""
    payload = {
        "id": quote.id,
        "quote_number": quote.quote_number,
        "client_id": quote.client_id,
        "status": quote.status,
        "job_type": quote.job_type,
        "description": quote.description,
        "property_sqft": quote.property_sqft,
        "ai_line_items": quote.ai_line_items or [],
        "ai_notes": quote.ai_notes,
        "subtotal": quote.subtotal,
        "tax_amount": quote.tax_amount,
        "discount_amount": quote.discount_amount,
        "total": quote.total,
        "valid_until": quote.valid_until,
        "sent_at": quote.sent_at,
        "internal_notes": quote.internal_notes,
        "created_at": quote.created_at,
        "client": None,
    }
    if include_client and quote.client:
        payload["client"] = ClientResponse.model_validate(quote.client).model_dump()
    return QuoteResponse(**payload)


# ─── List & Get ───────────────────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse)
async def list_quotes(
    request: Request,
    user: User = Depends(require_permission("quotes:read_own")),
    db: AsyncSession = Depends(get_db),
    client_id: UUID | None = None,
    status: QuoteStatus | None = None,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """List quotes for the current tenant. Optional filters: client_id, status."""
    try:
        repo = QuoteRepo(db, user.tenant_id)
        items, total = await repo.list_quotes(
            client_id=client_id,
            status=status,
            page=page,
            page_size=page_size,
        )
        quote_responses = [_quote_to_response(q) for q in items]
        pages = (total + page_size - 1) // page_size if total else 0
        return PaginatedResponse(
            items=quote_responses,
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.exception("quotes.list_failed", error=str(e), tenant_id=str(user.tenant_id))
        raise _user_error("Could not load quotes. Please try again.", status=500)


@router.get("/{quote_id}", response_model=QuoteResponse)
async def get_quote(
    request: Request,
    quote_id: UUID,
    user: User = Depends(require_permission("quotes:read_own")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single quote by ID, with client populated."""
    try:
        repo = QuoteRepo(db, user.tenant_id)
        quote = await repo.get_with_client(quote_id)
        if not quote:
            raise _not_found()
        return _quote_to_response(quote, include_client=True)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("quotes.get_failed", quote_id=str(quote_id), error=str(e))
        raise _user_error("Could not load quote. Please try again.", status=500)


# ─── Create ───────────────────────────────────────────────────────────────────

@router.post("", response_model=QuoteResponse, status_code=201)
@audit_log("quote.created", "quote")
async def create_quote(
    request: Request,
    body: CreateQuoteRequest,
    user: User = Depends(require_permission("quotes:create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new draft quote for a client."""
    client_repo = ClientRepo(db, user.tenant_id)
    client = await client_repo.get_by_id(body.client_id)
    if not client:
        raise _not_found("Client not found.")
    quote_repo = QuoteRepo(db, user.tenant_id)
    try:
        quote_number = await quote_repo.get_next_number()
        valid_until = datetime.now(timezone.utc) + timedelta(days=30)
        quote = await quote_repo.create(
            quote_number=quote_number,
            client_id=body.client_id,
            status=QuoteStatus.DRAFT,
            description=body.job_description,
            property_sqft=body.property_sqft,
            internal_notes=body.internal_notes,
            created_by_id=user.id,
            valid_until=valid_until,
            ai_line_items=[],
            subtotal=0,
            tax_amount=0,
            discount_amount=0,
            total=0,
        )
        await db.refresh(quote)
        log.info("quote.created", quote_id=str(quote.id), tenant_id=str(user.tenant_id))
        return _quote_to_response(quote)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("quote.create_failed", error=str(e), tenant_id=str(user.tenant_id))
        raise _user_error("Could not create quote. Please try again.")


# ─── Update ───────────────────────────────────────────────────────────────────

@router.patch("/{quote_id}", response_model=QuoteResponse)
@audit_log("quote.updated", "quote")
async def update_quote(
    request: Request,
    quote_id: UUID,
    body: UpdateQuoteRequest,
    user: User = Depends(require_permission("quotes:update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a draft quote (line items, notes, valid_until, discount). Only draft quotes can be edited."""
    repo = QuoteRepo(db, user.tenant_id)
    quote = await repo.get_by_id(quote_id)
    if not quote:
        raise _not_found()
    if quote.status != QuoteStatus.DRAFT:
        raise _user_error("Only draft quotes can be edited.")

    try:
        if body.line_items is not None:
            if len(body.line_items) > 500:
                raise _user_error("Too many line items (max 500).")
            items = [item.model_dump() if hasattr(item, "model_dump") else item for item in body.line_items]
            quote.ai_line_items = items
            subtotal = sum(float(item.get("total", 0)) for item in items)
            quote.subtotal = subtotal
            tenant_repo = TenantRepo(db)
            tenant = await tenant_repo.get_by_id(user.tenant_id)
            tax_rate = float(tenant.tax_rate or 0) if tenant else 0  # stored as 0–1
            quote.tax_amount = round(subtotal * tax_rate, 2)
            disc = float(body.discount_amount if body.discount_amount is not None else quote.discount_amount or 0)
            quote.discount_amount = disc
            quote.total = max(0, round(subtotal + quote.tax_amount - disc, 2))

        if body.internal_notes is not None:
            quote.internal_notes = body.internal_notes
        if body.valid_until is not None:
            quote.valid_until = body.valid_until
        if body.discount_amount is not None:
            quote.discount_amount = body.discount_amount
            if quote.ai_line_items:
                quote.total = max(0, round(quote.subtotal + quote.tax_amount - float(quote.discount_amount), 2))

        await db.flush()
        await db.refresh(quote)
        return _quote_to_response(quote)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("quote.update_failed", quote_id=str(quote_id), error=str(e))
        raise _user_error("Could not update quote. Please try again.")


# ─── AI Generate ──────────────────────────────────────────────────────────────

@router.post("/{quote_id}/generate", response_model=QuoteResponse)
async def generate_quote(
    request: Request,
    quote_id: UUID,
    body: AIGenerateRequest,
    user: User = Depends(require_permission("quotes:update")),
    db: AsyncSession = Depends(get_db),
):
    """Generate quote line items and notes using AI. Updates the draft quote."""
    repo = QuoteRepo(db, user.tenant_id)
    quote = await repo.get_with_client(quote_id)
    if not quote:
        raise _not_found()
    if quote.status != QuoteStatus.DRAFT:
        raise _user_error("Only draft quotes can be regenerated.")

    try:
        from services.ai_service import generate_quote_with_ai
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail={"message": "AI quote generation is not implemented yet. Add services.ai_service.generate_quote_with_ai."},
        )

    try:
        result = await generate_quote_with_ai(
            db=db,
            tenant_id=user.tenant_id,
            job_description=body.job_description,
            property_sqft=body.property_sqft,
            quote_id=quote_id,
        )
    except ValueError as e:
        log.warning("quote.generate_ai_invalid", quote_id=str(quote_id), error=str(e))
        raise _user_error(str(e) if str(e) else "AI returned an invalid response. Please try again.")
    except Exception as e:
        log.exception("quote.generate_ai_failed", quote_id=str(quote_id), error=str(e))
        raise _user_error("AI quote generation failed. Please try again later.", status=502)

    line_items = result.get("line_items")
    if not isinstance(line_items, list):
        line_items = []
    # Do not overwrite quote with empty AI result (e.g. timeout, API busy)
    if not line_items:
        raise _user_error(
            "AI did not return any line items. The service may be busy or the request timed out. "
            "Please try again or delete this draft."
        )
    quote.ai_line_items = line_items
    quote.ai_notes = result.get("notes")
    quote.ai_tokens_used = result.get("tokens_used")
    quote.description = body.job_description
    quote.property_sqft = body.property_sqft
    subtotal = sum(float(item.get("total", 0)) for item in line_items if isinstance(item, dict))
    quote.subtotal = subtotal
    tax_rate_pct = float(result.get("tax_rate", 0))
    if tax_rate_pct > 1:
        tax_rate_pct = tax_rate_pct / 100
    quote.tax_amount = round(subtotal * tax_rate_pct, 2)
    quote.total = max(0, round(subtotal + quote.tax_amount - float(quote.discount_amount or 0), 2))

    try:
        await db.flush()
        await db.refresh(quote)
    except Exception as e:
        log.exception("quote.generate_flush_failed", quote_id=str(quote_id), error=str(e))
        raise _user_error("Could not save generated quote. Please try again.", status=500)
    log.info("quote.ai_generated", quote_id=str(quote_id), items=len(quote.ai_line_items))
    return _quote_to_response(quote)


# ─── Approve (mark as sent) ────────────────────────────────────────────────────

@router.post("/{quote_id}/approve", response_model=QuoteResponse)
@audit_log("quote.approved", "quote")
async def approve_quote(
    request: Request,
    quote_id: UUID,
    user: User = Depends(require_permission("quotes:send")),
    db: AsyncSession = Depends(get_db),
):
    """Mark a draft quote as sent (approved). Requires line items."""
    repo = QuoteRepo(db, user.tenant_id)
    quote = await repo.get_by_id(quote_id)
    if not quote:
        raise _not_found()
    if quote.status != QuoteStatus.DRAFT:
        raise _user_error("Only draft quotes can be approved.")
    if not quote.ai_line_items:
        raise _user_error("Quote has no line items — generate with AI first.")

    try:
        quote.status = QuoteStatus.SENT
        quote.sent_at = datetime.now(timezone.utc)
        await db.flush()
        await db.refresh(quote)
        return _quote_to_response(quote)
    except Exception as e:
        log.exception("quote.approve_failed", quote_id=str(quote_id), error=str(e))
        raise _user_error("Could not approve quote. Please try again.", status=500)


# ─── Delete (draft only) ──────────────────────────────────────────────────────
# POST /quotes/delete with body { quote_id } is used by frontend to avoid path/routing issues.

@router.post("/delete", status_code=204)
@audit_log("quote.deleted", "quote")
async def delete_quote_by_body(
    request: Request,
    body: DeleteQuoteRequest,
    user: User = Depends(require_permission("quotes:update")),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a draft quote. Quote ID in request body (avoids path matching issues)."""
    await _do_delete_quote(body.quote_id, user, db)


async def _do_delete_quote(
    quote_id: UUID,
    user: User,
    db: AsyncSession,
) -> None:
    """Shared logic: permanently delete a draft quote. Raises HTTPException on failure."""
    repo = QuoteRepo(db, user.tenant_id)
    quote = await repo.get_by_id(quote_id)
    if not quote:
        raise _not_found()
    if quote.status != QuoteStatus.DRAFT:
        raise _user_error("Only draft quotes can be deleted.")
    try:
        await repo.hard_delete(quote_id)
        await db.flush()
        log.info("quote.deleted", quote_id=str(quote_id), tenant_id=str(user.tenant_id))
    except Exception as e:
        log.exception("quote.delete_failed", quote_id=str(quote_id), error=str(e))
        raise _user_error("Could not delete quote. Please try again.", status=500)


@router.delete("/{quote_id}", status_code=204)
@audit_log("quote.deleted", "quote")
async def delete_quote(
    request: Request,
    quote_id: UUID,
    user: User = Depends(require_permission("quotes:update")),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a draft quote (DELETE method)."""
    await _do_delete_quote(quote_id, user, db)


@router.post("/{quote_id}/delete", status_code=204)
@audit_log("quote.deleted", "quote")
async def delete_quote_post(
    request: Request,
    quote_id: UUID,
    user: User = Depends(require_permission("quotes:update")),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a draft quote (POST fallback for environments that block DELETE)."""
    await _do_delete_quote(quote_id, user, db)


# ─── Send (email / download) ──────────────────────────────────────────────────

@router.post("/{quote_id}/send")
async def send_quote(
    request: Request,
    quote_id: UUID,
    body: SendQuoteRequest,
    user: User = Depends(require_permission("quotes:send")),
    db: AsyncSession = Depends(get_db),
):
    """Send quote by email and/or return download link. Requires line items."""
    repo = QuoteRepo(db, user.tenant_id)
    quote = await repo.get_with_client(quote_id)
    if not quote:
        raise _not_found()
    if not quote.ai_line_items:
        raise _user_error("Quote has no line items. Generate with AI or add line items first.")

    result = {"quote_id": str(quote_id), "methods": []}

    if body.method in ("email", "both"):
        try:
            from services.email_service import send_quote_email
            await send_quote_email(quote, body.message)
            result["methods"].append("email")
        except ImportError:
            result["methods"].append("email")
            result["email_note"] = "Email service not configured; implement services.email_service.send_quote_email."
        except Exception as e:
            log.exception("quote.send_email_failed", quote_id=str(quote_id), error=str(e))
            raise _user_error("Could not send email. Please try again later.", status=502)

    if body.method in ("download", "both"):
        result["pdf_url"] = f"/api/v1/quotes/{quote_id}/pdf"
        result["methods"].append("download")

    return result


# ─── Download PDF ────────────────────────────────────────────────────────────

@router.get("/{quote_id}/pdf")
async def download_quote_pdf(
    request: Request,
    quote_id: UUID,
    user: User = Depends(require_permission("quotes:read_own")),
    db: AsyncSession = Depends(get_db),
):
    """Download quote as PDF. Requires line items."""
    repo = QuoteRepo(db, user.tenant_id)
    quote = await repo.get_with_client(quote_id)
    if not quote:
        raise _not_found()
    if not quote.ai_line_items:
        raise _user_error("Quote has no line items. Generate with AI or add line items first.")

    try:
        from services.pdf_service import generate_quote_pdf
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail={"message": "PDF generation is not implemented yet. Add services.pdf_service.generate_quote_pdf."},
        )

    try:
        pdf_bytes = await generate_quote_pdf(quote)
    except Exception as e:
        log.exception("quote.pdf_failed", quote_id=str(quote_id), error=str(e))
        raise _user_error("Could not generate PDF. Please try again.", status=500)

    if not pdf_bytes or not isinstance(pdf_bytes, (bytes, bytearray)):
        log.warning("quote.pdf_empty", quote_id=str(quote_id))
        raise _user_error("PDF generation returned no data.", status=500)

    return Response(
        content=bytes(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=quote-{quote.quote_number}.pdf"},
    )
