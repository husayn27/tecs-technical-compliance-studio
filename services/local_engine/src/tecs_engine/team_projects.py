from __future__ import annotations

import os

import httpx
import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from .shared_catalog import DEFAULT_PROJECT_URL

TEAM_KEYRING_SERVICE = "TECS Lighting Quotation"
TEAM_KEYRING_USER = "supabase-team-workspace-key"
DEFAULT_LEGACY_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFweWVuZ3NydWltYWh6ZnhiemFmIiw"
    "icm9sZSI6ImFub24iLCJpYXQiOjE3ODY2MTc2MzYsImV4cCI6MjEwMjE5MzYzNn0."
    "XAPkWP_6-g7p2qImG5KS6rFwIGT3AicYwxW6hHGfFUY"
)


class TeamProjectError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class TeamProjectService:
    """Secure client for the Supabase-backed shared project workspace."""

    def __init__(self, client_factory=httpx.Client) -> None:
        self.client_factory = client_factory

    @property
    def project_url(self) -> str:
        return os.getenv("TECS_SUPABASE_URL", DEFAULT_PROJECT_URL).rstrip("/")

    @property
    def anon_key(self) -> str:
        return os.getenv("TECS_SUPABASE_LEGACY_ANON_KEY", DEFAULT_LEGACY_ANON_KEY)

    @property
    def function_url(self) -> str:
        return f"{self.project_url}/functions/v1/tecs-team-projects"

    @staticmethod
    def _workspace_key() -> str:
        try:
            return keyring.get_password(TEAM_KEYRING_SERVICE, TEAM_KEYRING_USER) or ""
        except KeyringError:
            return ""

    def configured(self) -> bool:
        return bool(self._workspace_key())

    def configure(self, workspace_key: str) -> dict:
        cleaned = workspace_key.strip()
        if len(cleaned) < 48:
            raise TeamProjectError("The team workspace code is incomplete.", 400)
        try:
            keyring.set_password(TEAM_KEYRING_SERVICE, TEAM_KEYRING_USER, cleaned)
        except KeyringError as error:
            raise TeamProjectError(f"Could not store the team workspace code securely: {error}", 500) from error
        try:
            count = len(self.list_projects())
        except Exception:
            self.remove_key()
            raise
        return {"saved": True, "configured": True, "projects": count}

    def remove_key(self) -> dict:
        try:
            keyring.delete_password(TEAM_KEYRING_SERVICE, TEAM_KEYRING_USER)
        except PasswordDeleteError:
            pass
        except KeyringError as error:
            raise TeamProjectError(f"Could not remove the team workspace code: {error}", 500) from error
        return {"removed": True, "configured": False}

    def _headers(self) -> dict[str, str]:
        workspace_key = self._workspace_key()
        if not workspace_key:
            raise TeamProjectError("Configure the team workspace code in Settings first.", 428)
        return {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {self.anon_key}",
            "Content-Type": "application/json",
            "x-tecs-team-key": workspace_key,
        }

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            payload = response.json()
            detail = payload.get("detail") or payload.get("message") or payload.get("error")
        except ValueError:
            detail = response.text
        raise TeamProjectError(detail or "The shared project service could not complete the request.", response.status_code)

    def _request(self, method: str, *, project_id: str | None = None, payload: dict | None = None):
        params = {"id": project_id} if project_id else None
        try:
            with self.client_factory(timeout=30.0) as client:
                response = client.request(
                    method,
                    self.function_url,
                    params=params,
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.HTTPError as error:
            raise TeamProjectError(f"Could not reach the shared project service: {error}") from error
        self._raise(response)
        return response.json()

    def status(self) -> dict:
        configured = self.configured()
        return {"configured": configured}

    def list_projects(self) -> list[dict]:
        return self._request("GET")

    def get_project(self, project_id: str) -> dict:
        return self._request("GET", project_id=project_id)

    def save_project(self, payload: dict) -> dict:
        return self._request("POST", payload=payload)
