# LandscapeOS — SaaS Platform for Landscaping Businesses

## Project Overview
Multi-tenant SaaS platform serving landscaping companies. Each company is a **tenant**.
Tenants manage quotes, crews, schedules, and clients. Built mobile-first.

## Stack
- **Backend**: Python 3.11 / FastAPI / SQLAlchemy 2.0 / Alembic / Postgres (Supabase)
- **Frontend**: React 18 / Vite / TailwindCSS (web first, Expo later for iOS)
- **Auth**: Supabase Auth (JWT, MFA, OAuth)
- **AI**: Google Gemini for quote generation (AI Studio API key)
- **Storage**: Supabase Storage
- **Payments**: Stripe
- **SMS**: Twilio
- **Email**: Resend
- **Maps**: Mapbox
- **Deploy**: Render (backend), Cloudflare Pages (frontend)

## Multi-Tenancy Rules (CRITICAL — NEVER VIOLATE)
- Every DB model MUST have `tenant_id: UUID` (non-nullable, indexed)
- NEVER query DB directly in endpoints — always use Repository classes
- Repository `__init__` always takes `tenant_id` and stores as `self.tenant_id`
- Every repository method filters by `self.tenant_id` — no exceptions
- `tenant_id` ALWAYS resolved from JWT via `request.state.tenant_id`
- NEVER accept `tenant_id` as a user-supplied query parameter
- RLS enabled on ALL Supabase tables — enforced at DB level too

## Security Rules (NON-NEGOTIABLE)
- EVERY endpoint must have `Depends(require_permission("resource:action"))`
- JWT tokens expire in 1 hour, refresh tokens rotate on every use
- MFA required for `owner` and `admin` roles
- NEVER log PII (names, phones, emails, addresses)
- ALWAYS use parameterized queries — never f-string SQL
- File uploads: validate mime type with python-magic, strip EXIF, enforce 10MB limit
- Storage paths MUST be prefixed with `{tenant_id}/`
- Signed URLs only — never expose direct storage URLs (expire 1 hour)
- NEVER hardcode secrets — all from `Settings` class via environment
- Every write action decorated with `@audit_log("resource.action")`

## API Rules
- All routes under `/api/v1/`
- Auth: Bearer JWT only (no session cookies — mobile compatible)
- All responses: full nested JSON objects (mobile offline support)
- All list endpoints paginated (default `page_size: 20`, max `100`)
- Consistent error format: `{"detail": "message", "code": "ERROR_CODE"}`
- Health check at `/health`

## AI Model Routing
- `quote_generation`: gemini-2.0-flash (or gemini-2.5-flash for newer; set in ai_service MODEL_ROUTING)
- `quote_refinement`: gemini-2.0-flash
- NEVER hardcode model names — always use `MODEL_ROUTING` dict in ai_service
- Cache AI responses in DB with 24hr TTL by input hash (AICache model)

## Tenant Tiers
- `starter`: 3 users, 50 quotes/month, no custom branding, no route optimization
- `pro`: 10 users, 500 quotes/month, custom branding, route optimization, SMS
- `enterprise`: unlimited users/quotes, all features, prompt override

## Roles (per tenant)
- `owner`: full access + billing + delete tenant
- `admin`: full access except billing
- `crew_lead`: create quotes, manage own crew, view all schedules
- `laborer`: view own schedule, update job status

## Code Style
- Python: Black formatting, type hints everywhere, docstrings on all public functions
- TypeScript: strict mode, no `any`, named exports
- Use `async/await` everywhere — no sync DB or HTTP calls
- All DB connections via `get_db_for_tenant(tenant_id)` abstraction
- Migrations via Alembic — never edit DB schema manually
