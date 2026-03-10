# /deploy-check

Run all pre-deployment checks before pushing to production.

1. Check that no secrets are hardcoded: grep -r "sk-ant\|sk_live\|whsec_\|AC[a-z0-9]{32}" backend/ --include="*.py" (should return nothing)
2. Verify .env is in .gitignore
3. Check all endpoints have require_permission dependency
4. Check all models have tenant_id column
5. Check all repository methods filter by self.tenant_id
6. Run: cd backend && python -m pytest tests/ -v (if tests exist)
7. Check imports resolve: cd backend && python -c "from main import app; print('✓ App imports OK')"
8. List any TODO or FIXME comments that might indicate incomplete work

Report: PASS or FAIL with details for each check.
