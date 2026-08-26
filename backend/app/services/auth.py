from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import AuthSession, Device, User
from app.services.audit import record_audit
from app.services.ownership import get_owned_auth_session, get_owned_device

JWT_ALGORITHM = "HS256"
PASSWORD_HASH_ALGORITHM = "scrypt"
PASSWORD_HASH_N = 2**14
PASSWORD_HASH_R = 8
PASSWORD_HASH_P = 1
PASSWORD_HASH_SALT_BYTES = 16


class AuthenticationError(Exception):
    """Raised when credentials or an authenticated session are invalid."""


class AuthConfigurationError(Exception):
    """Raised when required authentication configuration is missing."""


@dataclass(frozen=True)
class AuthPrincipal:
    user_id: uuid.UUID
    session_id: uuid.UUID
    device_id: uuid.UUID


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    session: AuthSession
    user: User
    device: Device


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(PASSWORD_HASH_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=PASSWORD_HASH_N,
        r=PASSWORD_HASH_R,
        p=PASSWORD_HASH_P,
    )
    return "$".join(
        (
            PASSWORD_HASH_ALGORITHM,
            str(PASSWORD_HASH_N),
            str(PASSWORD_HASH_R),
            str(PASSWORD_HASH_P),
            salt.hex(),
            digest.hex(),
        )
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = password_hash.split("$")
        if algorithm != PASSWORD_HASH_ALGORITHM:
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError, IndexError):
        return False


DUMMY_PASSWORD_HASH = hash_password("voice-assistance-invalid-login")


def hash_refresh_token(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def secret_key(self) -> str:
        try:
            return self.settings.jwt_secret
        except RuntimeError as error:
            raise AuthConfigurationError(str(error)) from error

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _encode_access_token(self, user_id: uuid.UUID, session_id: uuid.UUID) -> tuple[str, int]:
        now = self._now()
        expires_in = self.settings.access_token_expire_minutes * 60
        payload = {
            "sub": str(user_id),
            "sid": str(session_id),
            "typ": "access",
            "iss": self.settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
            "jti": str(uuid.uuid4()),
        }
        token = jwt.encode(payload, self.secret_key, algorithm=JWT_ALGORITHM)
        return token, expires_in

    def decode_access_token(self, access_token: str) -> AuthPrincipal:
        try:
            payload = jwt.decode(
                access_token,
                self.secret_key,
                algorithms=[JWT_ALGORITHM],
                issuer=self.settings.jwt_issuer,
                options={"require": ["sub", "sid", "typ", "iss", "iat", "exp"]},
            )
            if payload.get("typ") != "access":
                raise AuthenticationError("Invalid access token")
            return AuthPrincipal(
                user_id=uuid.UUID(str(payload["sub"])),
                session_id=uuid.UUID(str(payload["sid"])),
                device_id=uuid.UUID(int=0),
            )
        except (jwt.InvalidTokenError, ValueError, KeyError, TypeError) as error:
            raise AuthenticationError("Invalid access token") from error

    async def resolve_access_token(
        self,
        session: AsyncSession,
        access_token: str,
    ) -> AuthPrincipal:
        principal = self.decode_access_token(access_token)
        result = await session.execute(
            select(AuthSession, User, Device)
            .join(User, User.id == AuthSession.user_id)
            .join(
                Device,
                (Device.id == AuthSession.device_id) & (Device.user_id == AuthSession.user_id),
            )
            .where(
                AuthSession.id == principal.session_id,
                AuthSession.user_id == principal.user_id,
            )
        )
        row = result.one_or_none()
        if row is None:
            raise AuthenticationError("Invalid authenticated session")
        auth_session, user, device = row
        now = self._now()
        if (
            user.status != "active"
            or auth_session.revoked_at is not None
            or auth_session.expires_at <= now
            or device.revoked_at is not None
        ):
            raise AuthenticationError("Invalid authenticated session")
        return AuthPrincipal(
            user_id=user.id,
            session_id=auth_session.id,
            device_id=device.id,
        )

    async def register_user(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
        request=None,
    ) -> User:
        normalized_email = normalize_email(email)
        existing = await session.scalar(select(User).where(User.email == normalized_email))
        if existing is not None:
            raise ValueError("An account with this login identity already exists")

        user = User(email=normalized_email, password_hash=hash_password(password), status="active")
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as error:
            await session.rollback()
            raise ValueError("An account with this login identity already exists") from error
        record_audit(
            session,
            "ACCOUNT_REGISTERED",
            user_id=user.id,
            metadata={"login_identity_type": "email"},
            request=request,
        )
        await session.commit()
        await session.refresh(user)
        return user

    async def login(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
        device_identifier: str,
        platform: str,
        device_name: str | None = None,
        device_metadata: dict | None = None,
        request=None,
    ) -> IssuedTokens:
        normalized_email = normalize_email(email)
        user = await session.scalar(select(User).where(User.email == normalized_email))
        password_valid = verify_password(
            password,
            user.password_hash if user is not None else DUMMY_PASSWORD_HASH,
        )
        if user is None or not password_valid or user.status != "active":
            record_audit(
                session,
                "LOGIN_FAILURE",
                user_id=user.id if user is not None else None,
                metadata={"reason": "invalid_credentials"},
                request=request,
            )
            await session.commit()
            raise AuthenticationError("Invalid credentials")

        device = await session.scalar(
            select(Device).where(Device.device_identifier == device_identifier)
        )
        if device is not None and device.user_id != user.id:
            record_audit(
                session,
                "LOGIN_FAILURE",
                user_id=user.id,
                metadata={"reason": "device_unavailable"},
                request=request,
            )
            await session.commit()
            raise AuthenticationError("Invalid credentials")
        if device is None:
            device = Device(
                user_id=user.id,
                device_identifier=device_identifier,
                platform=platform,
                name=device_name,
                device_metadata=device_metadata,
            )
            session.add(device)
            await session.flush()
            record_audit(
                session,
                "DEVICE_REGISTERED",
                user_id=user.id,
                device_id=device.id,
                metadata={"platform": platform, "source": "login"},
                request=request,
            )
        else:
            device.platform = platform
            device.name = device_name or device.name
            if device_metadata is not None:
                device.device_metadata = device_metadata
            device.revoked_at = None
            device.last_seen_at = self._now()

        issued = await self._create_session(session, user=user, device=device, request=request)
        record_audit(
            session,
            "LOGIN_SUCCESS",
            user_id=user.id,
            device_id=device.id,
            metadata={"session_id": str(issued.session.id)},
            request=request,
        )
        await session.commit()
        return issued

    async def _create_session(
        self,
        session: AsyncSession,
        *,
        user: User,
        device: Device,
        request=None,
    ) -> IssuedTokens:
        now = self._now()
        refresh_token = secrets.token_urlsafe(48)
        auth_session = AuthSession(
            user_id=user.id,
            device_id=device.id,
            refresh_token_hash=hash_refresh_token(refresh_token),
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=self.settings.refresh_token_expire_days),
        )
        session.add(auth_session)
        await session.flush()
        access_token, expires_in = self._encode_access_token(user.id, auth_session.id)
        return IssuedTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            session=auth_session,
            user=user,
            device=device,
        )

    async def refresh(
        self,
        session: AsyncSession,
        *,
        refresh_token: str,
        request=None,
    ) -> IssuedTokens:
        token_hash = hash_refresh_token(refresh_token)
        auth_session = await session.scalar(
            select(AuthSession)
            .where(AuthSession.refresh_token_hash == token_hash)
            .with_for_update()
        )
        if auth_session is None:
            record_audit(
                session,
                "TOKEN_REFRESH_FAILURE",
                metadata={"reason": "invalid_token"},
                request=request,
            )
            await session.commit()
            raise AuthenticationError("Invalid refresh token")

        user = await session.get(User, auth_session.user_id)
        device = await session.scalar(
            select(Device).where(
                Device.id == auth_session.device_id,
                Device.user_id == auth_session.user_id,
            )
        )
        now = self._now()
        if (
            user is None
            or device is None
            or user.status != "active"
            or auth_session.revoked_at is not None
            or auth_session.expires_at <= now
            or device.revoked_at is not None
        ):
            record_audit(
                session,
                "TOKEN_REFRESH_FAILURE",
                user_id=user.id if user is not None else None,
                device_id=device.id if device is not None else None,
                metadata={"reason": "revoked_or_expired_session"},
                request=request,
            )
            await session.commit()
            raise AuthenticationError("Invalid refresh session")

        auth_session.revoked_at = now
        auth_session.last_used_at = now
        replacement = await self._create_session(session, user=user, device=device, request=request)
        record_audit(
            session,
            "TOKEN_REFRESH",
            user_id=user.id,
            device_id=device.id,
            metadata={
                "replaced_session_id": str(auth_session.id),
                "session_id": str(replacement.session.id),
            },
            request=request,
        )
        await session.commit()
        return replacement

    async def logout(
        self,
        session: AsyncSession,
        principal: AuthPrincipal,
        *,
        request=None,
    ) -> None:
        auth_session = await get_owned_auth_session(
            session,
            user_id=principal.user_id,
            session_id=principal.session_id,
        )
        if auth_session is not None and auth_session.revoked_at is None:
            auth_session.revoked_at = self._now()
            record_audit(
                session,
                "LOGOUT",
                user_id=principal.user_id,
                device_id=principal.device_id,
                metadata={"session_id": str(principal.session_id)},
                request=request,
            )
            record_audit(
                session,
                "SESSION_REVOKED",
                user_id=principal.user_id,
                device_id=principal.device_id,
                metadata={"session_id": str(principal.session_id), "reason": "logout"},
                request=request,
            )
        await session.commit()

    async def revoke_session(
        self,
        session: AsyncSession,
        principal: AuthPrincipal,
        session_id: uuid.UUID,
        *,
        request=None,
    ) -> bool:
        auth_session = await get_owned_auth_session(
            session,
            user_id=principal.user_id,
            session_id=session_id,
        )
        if auth_session is None:
            return False
        if auth_session.revoked_at is None:
            auth_session.revoked_at = self._now()
            record_audit(
                session,
                "SESSION_REVOKED",
                user_id=principal.user_id,
                device_id=auth_session.device_id,
                metadata={"session_id": str(session_id), "reason": "user_request"},
                request=request,
            )
        await session.commit()
        return True

    async def revoke_device(
        self,
        session: AsyncSession,
        principal: AuthPrincipal,
        device_id: uuid.UUID,
        *,
        request=None,
    ) -> bool:
        device = await get_owned_device(
            session,
            user_id=principal.user_id,
            device_id=device_id,
        )
        if device is None:
            return False
        now = self._now()
        device.revoked_at = device.revoked_at or now
        associated_sessions = list(
            (
                await session.scalars(
                    select(AuthSession).where(
                        AuthSession.device_id == device.id,
                        AuthSession.user_id == principal.user_id,
                        AuthSession.revoked_at.is_(None),
                    )
                )
            ).all()
        )
        for auth_session in associated_sessions:
            auth_session.revoked_at = now
            record_audit(
                session,
                "SESSION_REVOKED",
                user_id=principal.user_id,
                device_id=device.id,
                metadata={"session_id": str(auth_session.id), "reason": "device_revoked"},
                request=request,
            )
        record_audit(
            session,
            "DEVICE_REVOKED",
            user_id=principal.user_id,
            device_id=device.id,
            metadata={"reason": "user_request"},
            request=request,
        )
        await session.commit()
        return True

    async def register_device(
        self,
        session: AsyncSession,
        principal: AuthPrincipal,
        *,
        device_identifier: str,
        platform: str,
        name: str | None = None,
        device_metadata: dict | None = None,
        request=None,
    ) -> tuple[Device, bool]:
        device = await session.scalar(
            select(Device).where(Device.device_identifier == device_identifier)
        )
        if device is not None and device.user_id != principal.user_id:
            raise AuthenticationError("Device is not available")
        created = device is None
        if device is None:
            device = Device(
                user_id=principal.user_id,
                device_identifier=device_identifier,
                platform=platform,
                name=name,
                device_metadata=device_metadata,
            )
            session.add(device)
            await session.flush()
        else:
            device.platform = platform
            device.name = name or device.name
            device.device_metadata = (
                device_metadata if device_metadata is not None else device.device_metadata
            )
            device.revoked_at = None
            device.last_seen_at = self._now()
        record_audit(
            session,
            "DEVICE_REGISTERED",
            user_id=principal.user_id,
            device_id=device.id,
            metadata={"platform": platform, "source": "device_endpoint", "created": created},
            request=request,
        )
        await session.commit()
        await session.refresh(device)
        return device, created

    async def list_devices(
        self,
        session: AsyncSession,
        principal: AuthPrincipal,
    ) -> list[Device]:
        result = await session.scalars(
            select(Device).where(Device.user_id == principal.user_id).order_by(Device.created_at)
        )
        return list(result.all())

    async def list_sessions(
        self,
        session: AsyncSession,
        principal: AuthPrincipal,
    ) -> list[AuthSession]:
        result = await session.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == principal.user_id)
            .order_by(AuthSession.created_at)
        )
        return list(result.all())
