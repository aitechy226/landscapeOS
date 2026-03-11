"""
AI quote generation using Google Gemini.
Uses tenant catalog (services, materials, labor) and caches responses in AICache (24hr TTL).

AICache contract: use only these fields for cache logic:
  - input_hash (not cache_key or prompt_hash)
  - response (not response_json) — store Python dict; DB JSON column stores JSON directly
  - expires_at — required, not nullable; set on every insert

Raises ValueError for invalid inputs or when AI returns invalid response; other errors propagate.
"""
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from config import settings
from models.models import AICache, ServiceCatalog, MaterialCatalog, LaborRate, Tenant

log = structlog.get_logger()

MODEL_ROUTING = {
    "quote_generation": "gemini-2.0-flash",
    "quote_refinement": "gemini-2.0-flash",
}
AI_CACHE_TTL_HOURS = 24
JOB_DESCRIPTION_MIN_LEN = 10
JOB_DESCRIPTION_MAX_LEN = 2000
PROPERTY_SQFT_MAX = 9_999_999


def _input_hash(
    tenant_id: UUID,
    job_description: str,
    property_sqft: int | None,
    model: str | None = None,
) -> str:
    """Stable hash for cache key (tenant-scoped in DB). Include model so different models don't collide."""
    payload = f"{tenant_id}|{(job_description or '').strip()}|{property_sqft or 0}|{model or ''}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _parse_json_from_response(raw: str) -> dict:
    """Extract JSON from raw text, stripping markdown code blocks if present. Raises ValueError on parse failure."""
    if not raw or not isinstance(raw, str):
        raise ValueError("Empty or invalid AI response.")
    clean = raw.strip().replace("```json", "").replace("```", "").strip()
    if not clean:
        raise ValueError("Empty or invalid AI response.")
    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        raise ValueError(f"AI returned invalid JSON: {e!s}") from e


async def _get_cached(
    db: AsyncSession, tenant_id: UUID, input_hash: str, model: str
) -> dict | None:
    """
    Return cached response if valid and not expired.
    Uses AICache.input_hash, AICache.response (dict/JSON), AICache.expires_at. Returns None on miss.
    """
    if not input_hash or not model:
        return None
    now = datetime.now(timezone.utc)
    try:
        result = await db.execute(
            select(AICache).where(
                AICache.tenant_id == tenant_id,
                AICache.input_hash == input_hash,
                AICache.model == model,
                AICache.expires_at > now,
            )
        )
        row = result.scalar_one_or_none()
    except Exception as e:
        log.warning("ai.cache_lookup_failed", error=str(e), tenant_id=str(tenant_id))
        return None

    if not row:
        return None

    raw = row.response
    if isinstance(raw, dict):
        if "line_items" in raw:
            log.info("ai.cache_hit", tenant_id=str(tenant_id), input_hash=input_hash[:8])
            return raw
        log.warning("ai.cache_invalid_shape", tenant_id=str(tenant_id))
        return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "line_items" in parsed:
                log.info("ai.cache_hit", tenant_id=str(tenant_id), input_hash=input_hash[:8])
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return None


async def _set_cache(
    db: AsyncSession,
    tenant_id: UUID,
    input_hash: str,
    model: str,
    response: dict,
    tokens_used: int | None = None,
) -> None:
    """
    Store response in AICache with TTL.
    Uses input_hash, response (dict → stored as JSON), expires_at (required). No-op if input invalid.
    """
    if not input_hash or not model:
        log.warning("ai.cache_save_skipped", reason="missing input_hash or model")
        return
    if not isinstance(response, dict) or "line_items" not in response:
        log.warning("ai.cache_save_skipped", reason="response missing line_items")
        return
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=AI_CACHE_TTL_HOURS)
    try:
        entry = AICache(
            tenant_id=tenant_id,
            input_hash=input_hash,
            model=model,
            response=response,
            tokens_used=tokens_used,
            expires_at=expires_at,
        )
        db.add(entry)
        await db.flush()
    except Exception as e:
        # Unique constraint (tenant_id, input_hash) or DB error — don't fail the request
        log.warning("ai.cache_save_failed", error=str(e), tenant_id=str(tenant_id))


def _safe_float(value, default: float = 0.0) -> float:
    """Coerce to float without raising; use default for None or invalid."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def generate_quote_with_ai(
    db: AsyncSession,
    tenant_id: UUID,
    job_description: str,
    property_sqft: int | None = None,
    quote_id: UUID | None = None,
) -> dict:
    """
    Generate quote line items and notes using Google Gemini.
    Loads tenant's service catalog, materials, and labor rates for context.
    Returns dict: line_items, notes, tokens_used, tax_rate (0–1), estimated_hours (optional).
    Raises ValueError for invalid input or when AI returns invalid response.
    """
    # Input validation
    if job_description is None:
        raise ValueError("Job description is required.")
    desc = (job_description or "").strip()
    if len(desc) < JOB_DESCRIPTION_MIN_LEN:
        raise ValueError(f"Job description must be at least {JOB_DESCRIPTION_MIN_LEN} characters.")
    if len(desc) > JOB_DESCRIPTION_MAX_LEN:
        raise ValueError(f"Job description must be at most {JOB_DESCRIPTION_MAX_LEN} characters.")
    if property_sqft is not None and (property_sqft < 0 or property_sqft > PROPERTY_SQFT_MAX):
        raise ValueError(f"Property size must be between 0 and {PROPERTY_SQFT_MAX}.")

    # Load key: settings (pydantic .env) -> process env -> direct .env load from backend dir
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or "") if settings else ""
    api_key = str(api_key).strip() or (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        try:
            from dotenv import load_dotenv
            backend_dir = Path(__file__).resolve().parent.parent
            env_file = backend_dir / ".env"
            if env_file.exists():
                load_dotenv(env_file)
            api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
        except Exception as e:
            log.warning("ai.dotenv_fallback_failed", error=str(e))
    if not api_key:
        log.warning("ai.missing_api_key", tenant_id=str(tenant_id))
        raise ValueError(
            "AI quote generation is not configured. Set GEMINI_API_KEY in backend/.env and restart the backend."
        )

    model = MODEL_ROUTING.get("quote_generation", "gemini-2.0-flash")
    key = _input_hash(tenant_id, desc, property_sqft, model)

    cached = await _get_cached(db, tenant_id, key, model)
    if cached is not None:
        return cached

    # Load tenant catalog
    services_result = await db.execute(
        select(ServiceCatalog).where(
            ServiceCatalog.tenant_id == tenant_id,
            ServiceCatalog.is_active == True,
        )
    )
    services = list(services_result.scalars().all())

    materials_result = await db.execute(
        select(MaterialCatalog).where(
            MaterialCatalog.tenant_id == tenant_id,
            MaterialCatalog.is_active == True,
        )
    )
    materials = list(materials_result.scalars().all())

    labor_result = await db.execute(
        select(LaborRate).where(LaborRate.tenant_id == tenant_id)
    )
    labor_rates = list(labor_result.scalars().all())

    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()

    tax_pct = _safe_float(tenant.tax_rate if tenant else 0, 0) * 100
    min_quote = _safe_float(tenant.minimum_quote if tenant else 150, 150)

    services_text = "\n".join(
        f"- {getattr(s, 'name', '') or 'Unnamed'}: ${_safe_float(getattr(s, 'base_price', 0))} per {getattr(s, 'unit', None) or 'each'}"
        for s in services
    ) or "No services configured yet"

    materials_text = "\n".join(
        f"- {getattr(m, 'name', '') or 'Unnamed'}: ${_safe_float(getattr(m, 'sell_price', 0))} per {getattr(m, 'unit', 'each')}"
        for m in materials
    ) or "No materials configured yet"

    labor_text = "\n".join(
        f"- {getattr(r, 'role', '') or 'Labor'} ({getattr(r, 'property_type', 'any')}): ${_safe_float(getattr(r, 'rate_per_hour', 0))}/hr"
        for r in labor_rates
    ) or "No labor rates configured yet"

    prompt = f"""You are a quoting assistant for a professional landscaping company.

Generate a detailed, itemized quote based on the job description below.

COMPANY CATALOG:
Services:
{services_text}

Materials:
{materials_text}

Labor Rates:
{labor_text}

Tax Rate: {tax_pct}%
Minimum Quote: ${min_quote}

JOB DESCRIPTION:
{desc}
{f"Property size: {property_sqft} sq ft" if property_sqft else ""}

Return ONLY a JSON object with this exact structure:
{{
  "line_items": [
    {{
      "description": "Service or item name",
      "quantity": 1.0,
      "unit": "each|sqft|yard|hour|bag|flat",
      "unit_price": 0.00,
      "total": 0.00
    }}
  ],
  "notes": "Any important notes about the job",
  "tax_rate": {tax_pct / 100 if tax_pct else 0},
  "estimated_hours": 0
}}

Rules:
- Use prices from the catalog when a service/material matches; otherwise use realistic landscaping rates.
- Be specific and detailed with line items.
- If property_sqft is provided, use it to estimate quantities where relevant.
- Ensure total = quantity * unit_price for each line item.
- tax_rate must be between 0 and 1 (e.g. 0.0875 for 8.75%).
- Return ONLY the JSON, no other text or markdown."""

    def _call_gemini() -> tuple[str, int | None]:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        # BLOCK_NONE so normal landscaping job descriptions (lawn, chemicals, tools) aren't blocked
        safety = {
            genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT: genai.types.HarmBlockThreshold.BLOCK_NONE,
            genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH: genai.types.HarmBlockThreshold.BLOCK_NONE,
            genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: genai.types.HarmBlockThreshold.BLOCK_NONE,
            genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: genai.types.HarmBlockThreshold.BLOCK_NONE,
        }
        gemini = genai.GenerativeModel(model, safety_settings=safety)
        response = gemini.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                max_output_tokens=1024,
                temperature=0.2,
            ),
            safety_settings=safety,
        )
        # Do NOT use response.text — it raises ValueError when there's no Part (e.g. blocked/empty).
        # Extract text only from candidates[].content.parts so we can handle empty/blocked ourselves.
        raw = ""
        candidates = getattr(response, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            if not content:
                continue
            parts = getattr(content, "parts", None) or []
            for part in parts:
                text = getattr(part, "text", None)
                if text and isinstance(text, str):
                    raw += text
            if raw:
                break
        if not raw and candidates:
            cand = candidates[0]
            reason = str(getattr(cand, "finish_reason", None) or "").upper()
            if "SAFETY" in reason:
                raise ValueError(
                    "AI output was blocked by safety filters. Try a shorter, more neutral job description."
                )
            raise ValueError(
                "AI returned no content. Try a shorter or simpler job description, then use Regenerate with AI if needed."
            )
        if not raw and getattr(response, "prompt_feedback", None):
            pf = response.prompt_feedback
            if getattr(pf, "block_reason", None):
                raise ValueError(
                    "AI blocked the input. Try a shorter or more neutral job description."
                ) from None
        if not raw:
            raise ValueError(
                "AI returned no content. Try again or use a simpler job description."
            )
        tokens_used = None
        um = getattr(response, "usage_metadata", None)
        if um:
            tokens_used = (
                getattr(um, "total_token_count", None)
                or (getattr(um, "prompt_token_count", 0) or 0) + (getattr(um, "candidates_token_count", 0) or 0)
            )
        return raw, tokens_used

    try:
        raw, tokens_used = await asyncio.to_thread(_call_gemini)
    except Exception as e:
        err_msg = str(e).lower()
        full_msg = str(e).replace("\n", " ").strip()[:200]
        if "api_key" in err_msg or "invalid" in err_msg or "403" in err_msg:
            log.warning("ai.gemini_auth_error", error=str(e), tenant_id=str(tenant_id))
            raise ValueError("AI service is not configured or key is invalid. Please check GEMINI_API_KEY.") from e
        if "quota" in err_msg or "429" in err_msg:
            log.warning("ai.gemini_rate_limit", error=str(e), tenant_id=str(tenant_id))
            raise ValueError("AI service is temporarily rate limited. Please try again later.") from e
        if "blocked" in err_msg or "safety" in err_msg or "content" in err_msg and "not" in err_msg:
            log.warning("ai.gemini_blocked", error=str(e), tenant_id=str(tenant_id))
            raise ValueError("AI blocked the request (safety filter). Try a different job description.") from e
        if "404" in err_msg or "not found" in err_msg:
            log.warning("ai.gemini_model_not_found", error=str(e), tenant_id=str(tenant_id))
            raise ValueError("AI model unavailable. Please try again later or contact support.") from e
        log.exception("ai.gemini_error", error=str(e), tenant_id=str(tenant_id))
        raise ValueError(f"AI service failed: {full_msg}. Please try again later.") from e

    try:
        data = _parse_json_from_response(raw)
    except ValueError as e:
        log.error("ai.parse_failed", error=str(e), raw=raw[:200])
        raise

    line_items = data.get("line_items") or []
    if not isinstance(line_items, list):
        line_items = []
    normalized = []
    for item in line_items:
        if not isinstance(item, dict):
            continue
        qty = _safe_float(item.get("quantity"), 1)
        up = _safe_float(item.get("unit_price"), 0)
        qty = max(0.0, min(qty, 999_999.0))
        up = max(0.0, min(up, 999_999.99))
        total = round(qty * up, 2)
        desc = str(item.get("description", "") or "").strip()[:500] or "Line item"
        unit = str(item.get("unit", "each") or "each")[:50]
        normalized.append({
            "description": desc,
            "quantity": qty,
            "unit": unit,
            "unit_price": up,
            "total": total,
        })
    line_items = normalized
    if not line_items:
        raise ValueError("AI returned no valid line items. The response may be incomplete or the service busy. Please try again.")

    tax_rate = _safe_float(data.get("tax_rate"), 0)
    if tax_rate > 1:
        tax_rate = tax_rate / 100
    tax_rate = min(1.0, max(0.0, tax_rate))

    raw_notes = data.get("notes")
    notes = str(raw_notes).strip()[:2000] if raw_notes is not None else ""

    raw_hours = data.get("estimated_hours")
    estimated_hours = None
    if raw_hours is not None:
        try:
            estimated_hours = float(raw_hours)
            if estimated_hours < 0:
                estimated_hours = None
        except (TypeError, ValueError):
            pass

    result = {
        "line_items": line_items,
        "notes": notes,
        "tokens_used": tokens_used,
        "tax_rate": tax_rate,
        "estimated_hours": estimated_hours,
    }

    try:
        await _set_cache(db, tenant_id, key, model, result, tokens_used=tokens_used)
    except Exception as e:
        log.warning("ai.cache_write_failed", error=str(e), tenant_id=str(tenant_id))
        # Best-effort: do not fail the request if cache write fails
    log.info("ai.quote_generated", tokens=tokens_used, items=len(line_items), tenant_id=str(tenant_id))
    return result
