"""Provider-neutral reminder push delivery contracts.

The project has no configured Android push provider yet.  This module keeps the
durable reminder worker independent of FCM and supplies a deterministic fake
for acceptance tests without persisting or logging token values.
"""

from __future__ import annotations

import email.utils
import json
import logging
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import httpx
import jwt

if TYPE_CHECKING:
    from app.core.config import Settings

LOGGER = logging.getLogger("voice-assistance-backend")


@dataclass(frozen=True)
class PushDeliveryResult:
    delivered: bool
    retryable: bool = False
    failure_code: str | None = None
    failure_reason: str | None = None


class PushDeliveryProvider(Protocol):
    async def send(
        self,
        *,
        token: str,
        delivery_id: str,
        title: str,
        body: str | None,
    ) -> PushDeliveryResult: ...


class UnavailablePushDeliveryProvider:
    """Safe production default until a real provider is configured."""

    async def send(
        self, *, token: str, delivery_id: str, title: str, body: str | None
    ) -> PushDeliveryResult:
        del token, delivery_id, title, body
        return PushDeliveryResult(
            delivered=False,
            retryable=False,
            failure_code="push_provider_unconfigured",
            failure_reason="push delivery provider is not configured",
        )


class FakePushDeliveryProvider:
    """Deterministic provider used by unit and integration acceptance tests."""

    def __init__(self, *, outcomes: list[PushDeliveryResult] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.deliveries: list[dict[str, str | None]] = []
        self._delivered_ids: set[str] = set()

    async def send(
        self, *, token: str, delivery_id: str, title: str, body: str | None
    ) -> PushDeliveryResult:
        if delivery_id in self._delivered_ids:
            return PushDeliveryResult(delivered=True)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if not outcome.delivered:
                return outcome
        self._delivered_ids.add(delivery_id)
        self.deliveries.append(
            {
                "delivery_id": delivery_id,
                "token_digest": sha256(token.encode()).hexdigest() if token else None,
                "title": title,
                "body": body,
            }
        )
        return PushDeliveryResult(delivered=True)


class FcmPushDeliveryProvider:
    """Delivers reminder push notifications via Firebase Cloud Messaging HTTP v1."""

    def __init__(
        self,
        *,
        project_id: str,
        client_email: str,
        private_key: str,
        token_uri: str = "https://oauth2.googleapis.com/token",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not project_id:
            raise ValueError("project_id is required for FcmPushDeliveryProvider")
        if not client_email:
            raise ValueError("client_email is required for FcmPushDeliveryProvider")
        if not private_key:
            raise ValueError("private_key is required for FcmPushDeliveryProvider")
        self.project_id = project_id
        self.client_email = client_email
        self.private_key = private_key
        self.token_uri = token_uri
        self._client = client
        self._owns_client = client is None
        self._access_token: str | None = None
        self._token_expiry: float = 0.0
        self._clock_skew: float = 0.0
        self._clock_skew_calibrated: bool = False

    @classmethod
    def from_service_account_dict(
        cls,
        data: dict[str, Any],
        *,
        project_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> FcmPushDeliveryProvider:
        return cls(
            project_id=project_id or data.get("project_id", ""),
            client_email=data.get("client_email", ""),
            private_key=data.get("private_key", ""),
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client=client,
        )

    @classmethod
    def from_service_account_file(
        cls,
        file_path: str | Path,
        *,
        project_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> FcmPushDeliveryProvider:
        path = Path(file_path).expanduser().resolve()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_service_account_dict(data, project_id=project_id, client=client)

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
            self._owns_client = True
        return self._client

    async def _calibrate_clock(self, http_client: httpx.AsyncClient) -> None:
        try:
            resp = await http_client.head("https://www.google.com", timeout=5.0)
            date_str = resp.headers.get("date")
            if date_str:
                server_dt = email.utils.parsedate_to_datetime(date_str)
                self._clock_skew = server_dt.timestamp() - time.time()
                self._clock_skew_calibrated = True
        except Exception:
            self._clock_skew_calibrated = True

    async def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < (self._token_expiry - 60):
            return self._access_token

        http_client = self._get_http_client()
        if not self._clock_skew_calibrated:
            await self._calibrate_clock(http_client)

        effective_now = int(time.time() + self._clock_skew)
        payload = {
            "iss": self.client_email,
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": self.token_uri,
            "iat": effective_now - 10,
            "exp": effective_now + 3600,
        }
        assertion = jwt.encode(payload, self.private_key, algorithm="RS256")

        response = await http_client.post(
            self.token_uri,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=10.0,
        )

        if response.status_code != 200:
            try:
                error_data = response.json()
            except Exception:
                error_data = {}
            if error_data.get("error") == "invalid_grant":
                self._clock_skew_calibrated = False
                await self._calibrate_clock(http_client)
                effective_now = int(time.time() + self._clock_skew)
                payload["iat"] = effective_now - 10
                payload["exp"] = effective_now + 3600
                assertion = jwt.encode(payload, self.private_key, algorithm="RS256")
                response = await http_client.post(
                    self.token_uri,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion,
                    },
                    timeout=10.0,
                )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Failed to acquire OAuth token from FCM: "
                    f"HTTP {response.status_code} {response.text[:200]}"
                )

        token_data = response.json()
        self._access_token = token_data["access_token"]
        expires_in = float(token_data.get("expires_in", 3600))
        self._token_expiry = time.time() + expires_in
        return self._access_token

    async def send(
        self,
        *,
        token: str,
        delivery_id: str,
        title: str,
        body: str | None,
    ) -> PushDeliveryResult:
        token_digest = sha256(token.encode()).hexdigest()[:12] if token else "none"
        LOGGER.info(
            "dispatching reminder push via FCM delivery_id=%s token_digest=%s",
            delivery_id,
            token_digest,
        )

        try:
            access_token = await self._get_access_token()
        except Exception as exc:
            LOGGER.warning(
                "fcm oauth authentication failure delivery_id=%s token_digest=%s error=%s",
                delivery_id,
                token_digest,
                exc,
            )
            return PushDeliveryResult(
                delivered=False,
                retryable=True,
                failure_code="fcm_auth_failed",
                failure_reason=str(exc),
            )

        notification: dict[str, str] = {"title": title}
        if body is not None:
            notification["body"] = body

        fcm_payload = {
            "message": {
                "token": token,
                "notification": notification,
                "data": {
                    "delivery_id": str(delivery_id),
                },
                "android": {
                    "priority": "HIGH",
                    "notification": {
                        "channel_id": "reminders",
                    },
                },
            }
        }

        endpoint = f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        http_client = self._get_http_client()
        try:
            response = await http_client.post(
                endpoint,
                json=fcm_payload,
                headers=headers,
                timeout=10.0,
            )
        except httpx.TimeoutException:
            LOGGER.warning(
                "fcm push timeout delivery_id=%s token_digest=%s",
                delivery_id,
                token_digest,
            )
            return PushDeliveryResult(
                delivered=False,
                retryable=True,
                failure_code="push_timeout",
                failure_reason="fcm push request timed out",
            )
        except httpx.NetworkError as exc:
            LOGGER.warning(
                "fcm push network error delivery_id=%s token_digest=%s error=%s",
                delivery_id,
                token_digest,
                exc,
            )
            return PushDeliveryResult(
                delivered=False,
                retryable=True,
                failure_code="push_network_error",
                failure_reason="fcm push network connection failed",
            )
        except Exception as exc:
            LOGGER.warning(
                "fcm push unexpected error delivery_id=%s token_digest=%s error=%s",
                delivery_id,
                token_digest,
                exc,
            )
            return PushDeliveryResult(
                delivered=False,
                retryable=True,
                failure_code="push_provider_error",
                failure_reason=str(exc),
            )

        if response.status_code == 200:
            LOGGER.info(
                "fcm push delivered successfully delivery_id=%s token_digest=%s",
                delivery_id,
                token_digest,
            )
            return PushDeliveryResult(delivered=True)

        status_code = response.status_code
        try:
            error_json = response.json()
        except Exception:
            error_json = {}

        error_obj = error_json.get("error", {})
        error_details = error_obj.get("details", [])
        error_code = None
        for detail in error_details:
            if isinstance(detail, dict) and "errorCode" in detail:
                error_code = detail["errorCode"]
                break

        msg = str(error_obj.get("message", "")).lower()
        is_unregistered = (
            error_code == "UNREGISTERED"
            or error_obj.get("status") == "NOT_FOUND"
            or "not registered" in msg
            or "registration token is not a valid" in msg
            or "invalid registration token" in msg
        )
        if is_unregistered:
            LOGGER.warning(
                "fcm device token unregistered delivery_id=%s token_digest=%s",
                delivery_id,
                token_digest,
            )
            return PushDeliveryResult(
                delivered=False,
                retryable=False,
                failure_code="push_token_unregistered",
                failure_reason="fcm reported device token unregistered or not found",
            )

        if status_code in (408, 429) or status_code >= 500:
            LOGGER.warning(
                "fcm transient error status=%s delivery_id=%s token_digest=%s",
                status_code,
                delivery_id,
                token_digest,
            )
            raw_text = response.text[:200]
            if token and token in raw_text:
                raw_text = raw_text.replace(token, "[REDACTED]")
            return PushDeliveryResult(
                delivered=False,
                retryable=True,
                failure_code=f"fcm_{status_code}",
                failure_reason=raw_text,
            )

        LOGGER.warning(
            "fcm permanent error status=%s delivery_id=%s token_digest=%s",
            status_code,
            delivery_id,
            token_digest,
        )
        return PushDeliveryResult(
            delivered=False,
            retryable=False,
            failure_code="push_provider_error",
            failure_reason=f"fcm permanent delivery error {status_code}",
        )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


def create_push_delivery_provider(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> PushDeliveryProvider:
    """Return FcmPushDeliveryProvider if configured and readable, else unavailable provider."""
    path = settings.fcm_service_account_path_resolved
    if path is not None and path.is_file():
        try:
            return FcmPushDeliveryProvider.from_service_account_file(
                path,
                project_id=settings.fcm_project_id,
                client=client,
            )
        except Exception as exc:
            LOGGER.warning(
                "Failed to initialize FCM provider from %s: %s; using unavailable provider",
                path,
                exc,
            )
            return UnavailablePushDeliveryProvider()
    return UnavailablePushDeliveryProvider()
