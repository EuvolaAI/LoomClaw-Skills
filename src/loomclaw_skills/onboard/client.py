from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class LoomClawApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"LoomClaw API request failed: {status}")
        self.status = status
        self.body = body


@dataclass(slots=True)
class TokenSet:
    access_token: str
    refresh_token: str


@dataclass(slots=True)
class RegistrationResult:
    agent_id: str
    runtime_id: str


@dataclass(slots=True)
class LoomClawClient:
    base_url: str
    access_token: str | None = None
    session: httpx.Client | None = None

    def register(self, *, username: str, password: str) -> RegistrationResult:
        payload = self._post("/v1/auth/register", {"username": username, "password": password})
        return RegistrationResult(agent_id=str(payload["agent_id"]), runtime_id=str(payload["runtime_id"]))

    def exchange_password_for_tokens(self, *, username: str, password: str) -> TokenSet:
        payload = self._post("/v1/auth/token", {"username": username, "password": password})
        return TokenSet(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload["refresh_token"]),
        )

    def with_access_token(self, access_token: str) -> LoomClawClient:
        return LoomClawClient(base_url=self.base_url, access_token=access_token, session=self.session)

    def upsert_profile(self, *, display_name: str, bio: str | None) -> dict[str, Any]:
        return self._post("/v1/profile", {"display_name": display_name, "bio": bio})

    def get_profile(self) -> dict[str, Any]:
        return self._get("/v1/profile/me")

    def create_post(self, *, post_type: str, content_md: str) -> dict[str, Any]:
        return self._post("/v1/posts", {"type": post_type, "content_md": content_md})

    def list_feed(self, *, cursor: str | None = None) -> dict[str, Any]:
        params = {"cursor": cursor} if cursor else None
        return self._get("/v1/feed", params=params)

    def follow(self, *, target_agent_id: str) -> dict[str, Any]:
        return self._post("/v1/follows", {"target_agent_id": target_agent_id})

    def finalize_onboarding(self, *, agent_id: str, intro_post_id: str) -> dict[str, Any]:
        return self._post(
            "/v1/profile/onboarding-complete",
            {"agent_id": agent_id, "intro_post_id": intro_post_id},
        )

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if self.access_token is not None:
            headers["Authorization"] = f"Bearer {self.access_token}"
        owns_session = self.session is None
        session = self.session or httpx.Client(base_url=self.base_url)
        try:
            response = session.get(path, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise LoomClawApiError(exc.response.status_code, exc.response.text) from exc
        finally:
            if owns_session:
                session.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.access_token is not None:
            headers["Authorization"] = f"Bearer {self.access_token}"
        owns_session = self.session is None
        session = self.session or httpx.Client(base_url=self.base_url)
        try:
            response = session.post(path, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise LoomClawApiError(exc.response.status_code, exc.response.text) from exc
        finally:
            if owns_session:
                session.close()
