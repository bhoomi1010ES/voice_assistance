from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import Settings
from app.services.push_delivery import (
    FcmPushDeliveryProvider,
    UnavailablePushDeliveryProvider,
    create_push_delivery_provider,
)

# Generate a valid RSA private key for JWT signing in tests
_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
MOCK_PRIVATE_KEY = _RSA_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")


@pytest.fixture
def local_tmp_dir(tmp_path: Path) -> Path:
    # AUD-PUSH-01: honor pytest's isolated writable basetemp on Windows.
    return tmp_path


def _sample_service_account() -> dict[str, str]:
    return {
        "type": "service_account",
        "project_id": "test-fcm-project",
        "private_key_id": "test-key-id",
        "private_key": MOCK_PRIVATE_KEY,
        "client_email": "test-fcm@test-fcm-project.iam.gserviceaccount.com",
        "client_id": "123456789",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def test_factory_returns_unavailable_when_no_credentials():
    settings = Settings(fcm_service_account_file=None)
    provider = create_push_delivery_provider(settings)
    assert isinstance(provider, UnavailablePushDeliveryProvider)


def test_factory_returns_unavailable_when_file_not_found(local_tmp_dir: Path):
    non_existent = local_tmp_dir / "missing.json"
    settings = Settings(fcm_service_account_file=str(non_existent))
    provider = create_push_delivery_provider(settings)
    assert isinstance(provider, UnavailablePushDeliveryProvider)


def test_factory_returns_unavailable_when_file_corrupt(local_tmp_dir: Path):
    corrupt_file = local_tmp_dir / "corrupt.json"
    corrupt_file.write_text("invalid json content", encoding="utf-8")
    settings = Settings(fcm_service_account_file=str(corrupt_file))
    provider = create_push_delivery_provider(settings)
    assert isinstance(provider, UnavailablePushDeliveryProvider)


def test_factory_returns_fcm_provider_when_file_valid(local_tmp_dir: Path):
    sa_file = local_tmp_dir / "service_account.json"
    sa_file.write_text(json.dumps(_sample_service_account()), encoding="utf-8")
    settings = Settings(
        fcm_service_account_file=str(sa_file),
        fcm_project_id="test-fcm-project",
    )
    provider = create_push_delivery_provider(settings)
    assert isinstance(provider, FcmPushDeliveryProvider)
    assert provider.project_id == "test-fcm-project"
    assert provider.client_email == "test-fcm@test-fcm-project.iam.gserviceaccount.com"


def test_factory_loads_workspace_service_account_file():
    # Tests loading real credentials configured in workspace .env
    settings = Settings()
    path = settings.fcm_service_account_path_resolved
    if path is not None and path.is_file():
        provider = create_push_delivery_provider(settings)
        assert isinstance(provider, FcmPushDeliveryProvider)
        assert provider.project_id == "voice-assistance-103d6"


@pytest.mark.asyncio
async def test_unavailable_provider_fails_safely():
    provider = UnavailablePushDeliveryProvider()
    result = await provider.send(
        token="device-push-token",
        delivery_id="delivery-123",
        title="Test Reminder",
        body="Reminder body content",
    )
    assert result.delivered is False
    assert result.retryable is False
    assert result.failure_code == "push_provider_unconfigured"


@pytest.mark.asyncio
async def test_fcm_send_success():
    sent_requests: list[httpx.Request] = []

    def mock_transport(request: httpx.Request) -> httpx.Response:
        sent_requests.append(request)
        url_str = str(request.url)
        if "oauth2.googleapis.com/token" in url_str:
            return httpx.Response(
                200,
                json={"access_token": "mock-oauth-token", "expires_in": 3600},
            )
        if "fcm.googleapis.com/v1/projects/test-fcm-project/messages:send" in url_str:
            return httpx.Response(
                200,
                json={"name": "projects/test-fcm-project/messages/msg-123"},
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_transport))
    provider = FcmPushDeliveryProvider.from_service_account_dict(
        _sample_service_account(),
        client=client,
    )
    # Bypass clock calibration for unit test
    provider._clock_skew_calibrated = True

    result = await provider.send(
        token="device-token-abc",
        delivery_id="deliv-777",
        title="Dentist Appointment",
        body="Leave now for 2pm dentist",
    )

    assert result.delivered is True
    assert result.failure_code is None

    # Verify request payload
    fcm_req = [r for r in sent_requests if "fcm.googleapis.com" in str(r.url)][0]
    body_data = json.loads(fcm_req.content)
    assert body_data["message"]["token"] == "device-token-abc"
    assert body_data["message"]["notification"]["title"] == "Dentist Appointment"
    assert body_data["message"]["notification"]["body"] == "Leave now for 2pm dentist"
    assert body_data["message"]["data"]["delivery_id"] == "deliv-777"
    assert body_data["message"]["android"]["priority"] == "HIGH"
    assert body_data["message"]["android"]["notification"]["channel_id"] == "reminders"
    assert fcm_req.headers["Authorization"] == "Bearer mock-oauth-token"


@pytest.mark.asyncio
async def test_fcm_send_unregistered_token_maps_to_permanent_failure():
    def mock_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "oauth2.googleapis.com/token" in url_str:
            return httpx.Response(
                200,
                json={"access_token": "mock-oauth-token", "expires_in": 3600},
            )
        return httpx.Response(
            404,
            json={
                "error": {
                    "code": 404,
                    "message": "Requested entity was not found.",
                    "status": "NOT_FOUND",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
                            "errorCode": "UNREGISTERED",
                        }
                    ],
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_transport))
    provider = FcmPushDeliveryProvider.from_service_account_dict(
        _sample_service_account(),
        client=client,
    )
    provider._clock_skew_calibrated = True

    result = await provider.send(
        token="dead-token-123",
        delivery_id="deliv-999",
        title="Check medication",
        body=None,
    )

    assert result.delivered is False
    assert result.retryable is False
    assert result.failure_code == "push_token_unregistered"


@pytest.mark.asyncio
async def test_fcm_send_server_error_maps_to_retryable():
    def mock_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "oauth2.googleapis.com/token" in url_str:
            return httpx.Response(
                200,
                json={"access_token": "mock-oauth-token", "expires_in": 3600},
            )
        return httpx.Response(503, text="Service Unavailable")

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_transport))
    provider = FcmPushDeliveryProvider.from_service_account_dict(
        _sample_service_account(),
        client=client,
    )
    provider._clock_skew_calibrated = True

    result = await provider.send(
        token="device-token-123",
        delivery_id="deliv-503",
        title="Meeting",
        body="Quarterly review",
    )

    assert result.delivered is False
    assert result.retryable is True
    assert result.failure_code == "fcm_503"


@pytest.mark.asyncio
async def test_fcm_send_timeout_maps_to_retryable():
    def mock_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "oauth2.googleapis.com/token" in url_str:
            return httpx.Response(
                200,
                json={"access_token": "mock-oauth-token", "expires_in": 3600},
            )
        raise httpx.ReadTimeout("Connection timed out")

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_transport))
    provider = FcmPushDeliveryProvider.from_service_account_dict(
        _sample_service_account(),
        client=client,
    )
    provider._clock_skew_calibrated = True

    result = await provider.send(
        token="device-token-123",
        delivery_id="deliv-timeout",
        title="Call mom",
        body=None,
    )

    assert result.delivered is False
    assert result.retryable is True
    assert result.failure_code == "push_timeout"


@pytest.mark.asyncio
async def test_raw_token_is_never_logged(caplog):
    def mock_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "oauth2.googleapis.com/token" in url_str:
            return httpx.Response(
                200,
                json={"access_token": "mock-oauth-token", "expires_in": 3600},
            )
        return httpx.Response(
            200,
            json={"name": "projects/test-fcm-project/messages/msg-123"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_transport))
    provider = FcmPushDeliveryProvider.from_service_account_dict(
        _sample_service_account(),
        client=client,
    )
    provider._clock_skew_calibrated = True

    secret_raw_token = "SUPER_SECRET_RAW_TOKEN_998877"
    with caplog.at_level("DEBUG"):
        await provider.send(
            token=secret_raw_token,
            delivery_id="deliv-log",
            title="Title",
            body="Body",
        )

    for record in caplog.records:
        assert secret_raw_token not in record.message


@pytest.mark.asyncio
async def test_fcm_send_429_rate_limit_maps_to_retryable():
    def mock_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "oauth2.googleapis.com/token" in url_str:
            return httpx.Response(
                200,
                json={"access_token": "mock-oauth-token", "expires_in": 3600},
            )
        return httpx.Response(429, text="Resource has been exhausted (e.g. check quota)")

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_transport))
    provider = FcmPushDeliveryProvider.from_service_account_dict(
        _sample_service_account(),
        client=client,
    )
    provider._clock_skew_calibrated = True

    result = await provider.send(
        token="device-token-quota",
        delivery_id="deliv-429",
        title="Title",
        body="Body",
    )

    assert result.delivered is False
    assert result.retryable is True
    assert result.failure_code == "fcm_429"


@pytest.mark.asyncio
async def test_fcm_send_invalid_token_error_message_maps_to_unregistered():
    def mock_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "oauth2.googleapis.com/token" in url_str:
            return httpx.Response(
                200,
                json={"access_token": "mock-oauth-token", "expires_in": 3600},
            )
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": 400,
                    "message": "The registration token is not a valid FCM registration token",
                    "status": "INVALID_ARGUMENT",
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_transport))
    provider = FcmPushDeliveryProvider.from_service_account_dict(
        _sample_service_account(),
        client=client,
    )
    provider._clock_skew_calibrated = True

    result = await provider.send(
        token="malformed-token-123",
        delivery_id="deliv-invalid",
        title="Title",
        body="Body",
    )

    assert result.delivered is False
    assert result.retryable is False
    assert result.failure_code == "push_token_unregistered"


@pytest.mark.asyncio
async def test_fcm_send_generic_4xx_maps_to_push_provider_error():
    def mock_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "oauth2.googleapis.com/token" in url_str:
            return httpx.Response(
                200,
                json={"access_token": "mock-oauth-token", "expires_in": 3600},
            )
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "message": (
                        "Firebase Cloud Messaging API has not been used in "
                        "project 123 before or it is disabled."
                    ),
                    "status": "PERMISSION_DENIED",
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_transport))
    provider = FcmPushDeliveryProvider.from_service_account_dict(
        _sample_service_account(),
        client=client,
    )
    provider._clock_skew_calibrated = True

    result = await provider.send(
        token="device-token-123",
        delivery_id="deliv-403",
        title="Title",
        body="Body",
    )

    assert result.delivered is False
    assert result.retryable is False
    assert result.failure_code == "push_provider_error"
    assert "device-token-123" not in (result.failure_reason or "")
