# /add-feature

Add a complete feature to LandscapeOS.

Read CLAUDE.md first, then implement: $ARGUMENTS

For every feature, create ALL of the following in order:
1. SQLAlchemy model in models/models.py (with tenant_id, TimestampMixin)
2. Pydantic schemas in schemas/schemas.py (Request + Response, with sanitization)
3. Repository class in repositories/repositories.py (extends TenantRepository)
4. API endpoints in api/v1/ (with require_permission on every route)
5. Audit log decorators on all write endpoints

Follow EXACTLY the same patterns as the existing Quote, Client, and User implementations.
Never skip the permission check. Never query DB directly in endpoints.
