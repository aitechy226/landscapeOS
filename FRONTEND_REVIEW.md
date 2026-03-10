# Frontend & Forms — Review and Fixes

This document summarizes **potential issues** found in the codebase and **what was fixed** so basic forms and auth work reliably.

---

## Root cause of “Your session has expired” in onboarding

**Problem:** The onboarding wizard used its own token logic (ref + `getToken` + `accessToken`), separate from the rest of the app. Token resolution could run at the wrong time or see stale values, so requests sometimes went out without a token → 401 → “Your session has expired.”

**Fix (done):**

1. **Single source of auth for all authenticated requests**  
   Every authenticated call now goes through `useApi()`. There is no second path.

2. **Token resolved at call time from storage first**  
   `useApi()` now passes:
   - `getStoredToken() || auth?.access_token`
   so the token is read from `sessionStorage` when you actually call `api(...)`, not from a stale ref or prop.

3. **Onboarding wizard uses the same `useApi()`**  
   The wizard no longer has:
   - `accessToken` / `getToken` props  
   - `tokenRef` or any custom token handling  
   It only does:
   - `const api = useApi();`
   - `await api(path, { method: 'POST', body });`
   So it behaves like Clients and Settings: one hook, one place that attaches the token.

---

## Other potential issues (and current status)

| Area | Risk | Status |
|------|------|--------|
| **API_BASE** | Hardcoded fallback `http://localhost:8000/api/v1`; wrong in prod if `window.API_BASE` not set | Document in deploy/SETUP; set `API_BASE` in HTML or env for prod |
| **CORS** | Frontend must be in `ALLOWED_ORIGINS` (e.g. `http://localhost:3000`) | Already in config; ensure port matches where you serve the app |
| **sessionStorage** | Token lost on new tab or if user clears storage | Expected; show “Session expired” and redirect to login (already done for 401) |
| **Login** | Uses `apiFetch` without token (correct). Others use `useApi()` | Consistent after wizard change |
| **Validation** | Phone 10–15 digits, email format, required fields are enforced on frontend and backend | In place for login, signup, onboarding step 1 & 5, client form, settings |
| **Error messages** | 401 → “Your session has expired”; 422 → first validation msg; 400 → `detail.message` or generic | Handled in `apiFetch` and forms |

---

## What to do if “session expired” still appears

1. **Confirm token is sent**  
   In DevTools → Network, select the failing request (e.g. `onboarding/step/1`). In Request Headers, check for `Authorization: Bearer <token>`.

2. **If the header is missing**  
   Then the client is not sending a token. Check:
   - After login, does `sessionStorage` have key `ls_auth` and an object with `access_token`?
   - Are you on the same origin (same port/protocol) as where you logged in? (sessionStorage is origin-scoped.)

3. **If the header is present and you still get 401**  
   Then the backend is rejecting the token (expired, wrong secret, or user not found). Check backend logs and `SUPABASE_JWT_SECRET` / Supabase JWT expiry.

---

## Files changed in this review

- **frontend/index.html**
  - `useApi()`: token order is now `getStoredToken() || auth?.access_token`.
  - `OnboardingWizard`: removed `accessToken`, `getToken`, `tokenRef`; uses only `useApi()` and `submitWithAuth` → `api(path, { method: 'POST', body })`.
  - App: no longer passes `accessToken` or `getToken` to `OnboardingWizard`.

No backend changes were required for this fix.
