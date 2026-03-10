"""
Supabase Auth service wrapper.
Isolates all Supabase SDK calls — easy to swap provider if needed.
"""
import httpx
import structlog
from config import settings

log = structlog.get_logger()


class EmailNotConfirmedError(Exception):
    """Raised when sign-in fails because the user has not confirmed their email."""

    def __init__(self, message: str = "Email not confirmed"):
        self.message = message
        super().__init__(message)


class SupabaseService:
    """Wraps Supabase Auth REST API calls."""

    def __init__(self):
        self.url = settings.SUPABASE_URL
        self.service_key = settings.SUPABASE_SERVICE_KEY
        self.anon_key = settings.SUPABASE_ANON_KEY

    @property
    def _auth_headers(self):
        return {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
        }

    async def create_user(self, email: str, password: str) -> dict:
        """Create user via public signup endpoint — sends confirmation email."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.url}/auth/v1/signup",
                headers={
                    "apikey": self.anon_key,
                    "Content-Type": "application/json",
                },
                json={"email": email, "password": password},
            )
            data = resp.json()
            if resp.status_code not in (200, 201):
                raise Exception(data.get("msg", "Failed to create user"))
            return data

    async def get_user_by_id(self, supabase_user_id: str) -> dict | None:
        """Fetch a user from Supabase Admin API by id. Returns None on 404 or error."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.url}/auth/v1/admin/user/{supabase_user_id}",
                    headers=self._auth_headers,
                )
                if resp.status_code == 200:
                    return resp.json()
                return None
        except Exception:
            return None

    async def delete_user(self, supabase_user_id: str) -> None:
        """Permanently delete a user from Supabase Auth (admin API)."""
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{self.url}/auth/v1/admin/user/{supabase_user_id}",
                headers=self._auth_headers,
            )
            if resp.status_code not in (200, 204, 404):
                raise Exception(resp.json().get("msg", "Failed to delete auth user"))

    async def invite_user(self, email: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.url}/auth/v1/admin/users",
                headers=self._auth_headers,
                json={
                    "email": email,
                    "email_confirm": False,
                    "invite": True,
                },
            )
            data = response.json()
            if response.status_code not in (200, 201):
                raise Exception(data.get("message", "Failed to invite user"))
            return data

    async def sign_in(self, email: str, password: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.url}/auth/v1/token?grant_type=password",
                headers={
                    "apikey": self.anon_key,
                    "Content-Type": "application/json",
                },
                json={"email": email, "password": password},
            )
            data = response.json()
            if response.status_code != 200:
                # Supabase can return different keys: error/error_description (OAuth) or msg/message
                err = (data.get("error") or data.get("error_code") or "").lower()
                err_desc = (
                    data.get("error_description")
                    or data.get("msg")
                    or data.get("message")
                    or "Invalid credentials"
                )
                err_desc = str(err_desc)
                # Explicit code or message hint that email is not confirmed
                if (
                    err == "email_not_confirmed"
                    or "confirm" in err_desc.lower()
                    or "not confirmed" in err_desc.lower()
                ):
                    raise EmailNotConfirmedError(err_desc or "Email not confirmed")
                raise Exception(err_desc)
            return {
                "access_token": data["access_token"],
                "refresh_token": data["refresh_token"],
                "user": data["user"],
            }

    async def refresh_session(self, refresh_token: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.url}/auth/v1/token?grant_type=refresh_token",
                headers={
                    "apikey": self.anon_key,
                    "Content-Type": "application/json",
                },
                json={"refresh_token": refresh_token},
            )
            data = response.json()
            if response.status_code != 200:
                raise Exception("Invalid refresh token")
            return {
                "access_token": data["access_token"],
                "refresh_token": data["refresh_token"],
            }

    async def sign_out(self, supabase_user_id: str):
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.url}/auth/v1/admin/users/{supabase_user_id}/logout",
                headers=self._auth_headers,
            )

    async def send_password_reset(self, email: str):
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.url}/auth/v1/recover",
                headers={"apikey": self.anon_key, "Content-Type": "application/json"},
                json={"email": email},
            )

    async def reset_password(self, email: str, token: str, new_password: str) -> dict:
        """Confirm password reset with token from reset email (Supabase admin reset)."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.url}/auth/v1/admin/reset",
                headers=self._auth_headers,
                json={"email": email, "token": token, "new_password": new_password},
            )
            if resp.status_code not in (200, 201):
                raise Exception("Could not reset password")
            return resp.json()

    async def resend_confirmation(self, email: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.url}/auth/v1/resend",
                headers={
                    "apikey": self.anon_key,
                    "Content-Type": "application/json",
                },
                json={"type": "signup", "email": email},
            )
            if resp.status_code not in (200, 201):
                raise Exception("Could not resend confirmation email")
            return resp.json()




    