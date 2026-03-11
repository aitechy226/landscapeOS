# LandscapeOS — Complete Setup & Deployment Guide

---

## Prerequisites

- Python 3.11+
- Docker + Docker Compose (for local dev)
- Git
- A domain name (e.g. `landscapeos.com`)
- Accounts: Supabase, Render, Cloudflare, Google (AI Studio for Gemini), Stripe

---

## Part 1: Local Development Setup

### Step 1 — Clone and configure

```bash
git clone <your-repo>
cd landscapeos/backend
cp .env.example .env
```

Edit `.env` with your credentials (Supabase, Gemini, etc).
At minimum, set these for local dev:
```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/landscapeos
APP_ENV=development
DEBUG=true
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SERVICE_KEY=YOUR_SERVICE_KEY
SUPABASE_ANON_KEY=YOUR_ANON_KEY
SUPABASE_JWT_SECRET=YOUR_JWT_SECRET
GEMINI_API_KEY=AIza...   # from https://aistudio.google.com/apikey
SUPERADMIN_KEY=pick-a-long-random-string
```

### Step 2 — Install Python dependencies

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Step 3 — Run with Docker (recommended)

```bash
# From project root
docker-compose up --build
```

This starts:
- Postgres on localhost:5432
- FastAPI on localhost:8000
- Frontend on localhost:3000

### Step 4 — Run database migrations

```bash
cd backend
alembic upgrade head
```

Or for first-time setup (dev only):
```bash
python -c "import asyncio; from db.database import init_db; asyncio.run(init_db())"
```

### Step 5 — Verify it's working

```bash
curl http://localhost:8000/health
# Should return: {"status":"healthy","database":true,"version":"1.0.0"}
```

Open http://localhost:3000 — you should see the LandscapeOS login page.

---

## Part 2: Supabase Setup

### Step 1 — Create project

1. Go to https://supabase.com and create a new project
2. Choose a region close to your users (US East for NJ-based business)
3. Save your database password

### Step 2 — Get credentials

In Supabase dashboard → Settings → API:
- Copy `Project URL` → `SUPABASE_URL`
- Copy `anon public` key → `SUPABASE_ANON_KEY`
- Copy `service_role` key → `SUPABASE_SERVICE_KEY`
- Copy `JWT Secret` → `SUPABASE_JWT_SECRET`

In Settings → Database:
- Copy connection string (port **6543** for pooling) → `DATABASE_URL`
- Replace `[YOUR-PASSWORD]` with your database password

### Step 3 — Enable Row Level Security

In Supabase SQL Editor, run this once:

```sql
-- Enable RLS on all tenant tables
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE quotes ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE service_catalog ENABLE ROW LEVEL SECURITY;
ALTER TABLE material_catalog ENABLE ROW LEVEL SECURITY;
ALTER TABLE labor_rates ENABLE ROW LEVEL SECURITY;
ALTER TABLE crews ENABLE ROW LEVEL SECURITY;
ALTER TABLE background_jobs ENABLE ROW LEVEL SECURITY;

-- Tenant isolation policy — each tenant only sees their own data
CREATE POLICY "tenant_isolation" ON users
  USING (tenant_id::text = auth.jwt() ->> 'tenant_id');

CREATE POLICY "tenant_isolation" ON clients
  USING (tenant_id::text = auth.jwt() ->> 'tenant_id');

CREATE POLICY "tenant_isolation" ON quotes
  USING (tenant_id::text = auth.jwt() ->> 'tenant_id');

CREATE POLICY "tenant_isolation" ON jobs
  USING (tenant_id::text = auth.jwt() ->> 'tenant_id');

CREATE POLICY "tenant_isolation" ON service_catalog
  USING (tenant_id::text = auth.jwt() ->> 'tenant_id');

CREATE POLICY "tenant_isolation" ON material_catalog
  USING (tenant_id::text = auth.jwt() ->> 'tenant_id');

CREATE POLICY "tenant_isolation" ON labor_rates
  USING (tenant_id::text = auth.jwt() ->> 'tenant_id');

CREATE POLICY "tenant_isolation" ON crews
  USING (tenant_id::text = auth.jwt() ->> 'tenant_id');

-- Audit logs and AI cache — service role only (your backend)
CREATE POLICY "service_only" ON audit_logs
  USING (auth.role() = 'service_role');

CREATE POLICY "service_only" ON ai_cache
  USING (auth.role() = 'service_role');
```

### Step 4 — Configure Auth settings

In Supabase → Authentication → Settings:
- Set `Site URL`: `https://app.landscapeos.com`
- Add redirect URLs: `https://app.landscapeos.com/**`
- Enable email confirmations (recommended for production)

---

## Part 3: Deploy Backend to Render

### Step 1 — Create Render account

Go to https://render.com and connect your GitHub.

### Step 2 — Create Web Service

1. New → Web Service
2. Connect your repo
3. Settings:
   - **Name**: `landscapeos-api`
   - **Region**: US East (Ohio)
   - **Branch**: `main`
   - **Root Directory**: `backend`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Plan**: Starter ($7/month) to start

### Step 3 — Set Environment Variables

In Render dashboard → your service → Environment, add ALL variables from `.env.example`:

```
APP_ENV=production
DEBUG=false
DATABASE_URL=postgresql+asyncpg://postgres:[PASS]@db.[REF].supabase.co:6543/postgres
SUPABASE_URL=https://[REF].supabase.co
SUPABASE_SERVICE_KEY=[your service key]
SUPABASE_ANON_KEY=[your anon key]
SUPABASE_JWT_SECRET=[your jwt secret]
GEMINI_API_KEY=AIza[...]   # from https://aistudio.google.com/apikey
STRIPE_SECRET_KEY=sk_live_[...]
STRIPE_WEBHOOK_SECRET=whsec_[...]
TWILIO_ACCOUNT_SID=AC[...]
TWILIO_AUTH_TOKEN=[...]
TWILIO_FROM_NUMBER=+1[...]
RESEND_API_KEY=re_[...]
SUPERADMIN_KEY=[long random string]
FIELD_ENCRYPTION_KEY=[fernet key]
ALLOWED_ORIGINS=["https://app.landscapeos.com"]
SECRET_KEY=[random 32 chars]
```

**Generate keys:**
```bash
# Secret key
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Fernet encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Step 4 — Set custom domain

In Render → your service → Custom Domain:
- Add `api.landscapeos.com`
- Copy the CNAME record they give you

### Step 5 — Run migrations on first deploy

In Render → your service → Shell:
```bash
alembic upgrade head
```

---

## Part 4: Deploy Frontend to Cloudflare Pages

### Step 1 — Create Cloudflare account

Go to https://cloudflare.com

### Step 2 — Set your domain's nameservers

In your domain registrar, point nameservers to Cloudflare's (provided during setup).

### Step 3 — Deploy frontend

In Cloudflare dashboard → Pages → Create project:
1. Connect GitHub repo
2. Settings:
   - **Project name**: `landscapeos-app`
   - **Root directory**: `frontend`
   - **Build command**: (leave empty — static HTML)
   - **Output directory**: `/`
3. Add environment variable:
   - `API_BASE` = `https://api.landscapeos.com/api/v1`
4. Deploy

### Step 4 — Set custom domain

In Cloudflare Pages → your project → Custom domains:
- Add `app.landscapeos.com`

### Step 5 — DNS Records (in Cloudflare DNS)

```
Type    Name    Value                           Proxy
A       @       [your Render IP]                ✓ (proxied)
CNAME   app     landscapeos-app.pages.dev       ✓ (proxied)
CNAME   api     [your-service].onrender.com     ✓ (proxied)
CNAME   www     landscapeos.com                 ✓ (proxied)
```

### Step 6 — Cloudflare WAF Rules (free tier)

In Cloudflare → Security → WAF → Custom Rules:

**Rule 1 — Block SQL injection:**
```
Field: URI Full
Operator: contains
Value: SELECT * FROM
→ Action: Block
```

**Rule 2 — Rate limit auth endpoints:**
```
Field: URI Path
Operator: contains
Value: /auth/login
Rate limit: 10 requests per minute
→ Action: Block
```

**Rule 3 — Force HTTPS:**
In Edge Certificates → Always Use HTTPS: ON

---

## Part 5: Stripe Setup

### Step 1 — Create products

In Stripe → Products → Create:
1. **LandscapeOS Starter** — $99/month
   - Copy Price ID → `STRIPE_STARTER_PRICE_ID`
2. **LandscapeOS Pro** — $199/month  
   - Copy Price ID → `STRIPE_PRO_PRICE_ID`
3. **LandscapeOS Enterprise** — $499/month
   - Copy Price ID → `STRIPE_ENTERPRISE_PRICE_ID`

### Step 2 — Webhook

In Stripe → Developers → Webhooks → Add endpoint:
- URL: `https://api.landscapeos.com/api/v1/webhooks/stripe`
- Events: `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`
- Copy webhook secret → `STRIPE_WEBHOOK_SECRET`

---

## Part 6: Ongoing Operations

### Deploy a code change
```bash
git push origin main
# Render auto-deploys in ~90 seconds
# Cloudflare Pages auto-deploys in ~30 seconds
```

### Run a database migration
```bash
# Locally: write migration
alembic revision --autogenerate -m "add new table"
git push  # deploys

# On Render (via Shell):
alembic upgrade head
```

### View logs
```bash
# Render dashboard → Logs (live streaming)
# Or install Render CLI:
render logs landscapeos-api --tail
```

### Access superadmin panel
```bash
curl https://api.landscapeos.com/api/v1/admin/stats \
  -H "X-Admin-Key: YOUR_SUPERADMIN_KEY"
```

### Rotate API keys (every 90 days)
1. Generate new key in provider dashboard
2. Update in Render Environment Variables
3. Render auto-restarts — zero downtime

### Monitor uptime (free)
1. Go to https://uptimerobot.com
2. Add monitor → HTTPS → `https://api.landscapeos.com/health`
3. Alert email: your@email.com
4. Check interval: 5 minutes

### View costs
- Render: https://dashboard.render.com/billing
- Supabase: https://supabase.com/dashboard/org/_/billing
- Google AI: https://aistudio.google.com/ (API usage / billing)

---

## Part 7: Claude Code Integration

### CLAUDE.md is in the repo root — Claude Code reads it on every session.

### Slash commands available:
```bash
/add-feature    # Add a full feature: DB model + repo + API endpoint + schema
/add-endpoint   # Add a single API endpoint with proper auth + validation
/deploy-check   # Run tests, lint, check env vars before pushing
```

### Recommended first Claude Code session:
```
> Read CLAUDE.md, then add the complete Quote CRUD endpoints
  following the same patterns as tenant.py — repository layer,
  Pydantic schemas, permission checks, and audit logging.
  The Quote model is already in models.py.
```

---

## Security Checklist (Before First Client)

```
□ .env is in .gitignore (verify: git status should not show .env)
□ All production env vars set in Render (not hardcoded anywhere)
□ Supabase RLS enabled on all tables (run SQL in Part 2, Step 3)
□ Cloudflare WAF rules active
□ HTTPS enforced (Cloudflare "Always Use HTTPS" on)
□ Superadmin key is long and random (not "admin123")
□ Stripe using live keys in production (not test keys)
□ UptimeRobot monitoring active
□ Render health check configured (/health endpoint)
```

---

## Monthly Cost Summary

| Stage | Tenants | Monthly Cost | Revenue (est) |
|-------|---------|-------------|---------------|
| Dev   | 0       | ~$0         | $0            |
| Early | 1–10    | ~$30–50     | $99–$990      |
| Growth| 10–100  | ~$75–150    | $990–$14,900  |
| Scale | 100–500 | ~$200–500   | $14,900–$74,500 |

**Break-even: your first paying customer.**
