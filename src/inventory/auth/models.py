"""Authentication-facing identity and session models."""

from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from inventory.access_control.models import Permission, Role, TableAccessGrant

if TYPE_CHECKING:
    from inventory.access_control.store import RoleStore
    from inventory.auth.google import GoogleIdentity, GoogleTokenVerifier


@dataclass
class User:
    """User identity and profile, with optional password attribute."""

    user_id: int
    username: str
    email: str
    role_id: int
    created_at: datetime
    password: str = ""

    def has_permission(self, code: str, role: Role) -> bool:
        """Delegate the permission check to the user's assigned role."""
        if self.role_id != role.role_id:
            return False
        return role.has_permission(code, None)

    def create_role(
        self,
        name: str,
        permissions: list[str],
        role_id: int = 0,
        job_priority: int = 3,
        grant_id: int = 0,
        store: RoleStore | None = None,
    ) -> Role:
        """Verify caller authority, create the role and grants, save it, and return it."""
        if self.role_id != 1:
            raise PermissionError("Only administrators can create roles.")

        new_role = Role(
            role_id=role_id,
            role_name=name,
            created_by=self.user_id,
            is_system_default=False,
            default_job_priority=job_priority,
            grants=[],
        )
        for i, code in enumerate(dict.fromkeys(permissions), start=1):
            perm = Permission(permission_id=i, code=code, description="")
            grant = TableAccessGrant(
                grant_id=grant_id,
                role_id=role_id,
                permission_id=i,
                table_name=None,
                granted_by=self.user_id,
                granted_at=datetime.now(),
                permission=perm,
                permission_code=code,
            )
            new_role.add_permission(grant)

        if store is None:
            from inventory.access_control.store import RoleStore
            store = RoleStore("roles.json")
        store.save(new_role)
        return new_role

    def assign_role(self, user: "User", role: Role) -> None:
        """Verify caller authority and update the target user's assigned role."""
        if self.role_id != 1:
            raise PermissionError("Only administrators can assign roles.")
        user.role_id = role.role_id

    def delete_role(self, role: Role, store: RoleStore | None = None) -> None:
        """Verify caller authority, prevent deletion of protected roles, and remove from storage."""
        if self.role_id != 1:
            raise PermissionError("Only administrators can delete roles.")
        if role.is_system_default:
            raise ValueError("Cannot delete a system default role.")

        if store is None:
            from inventory.access_control.store import RoleStore
            store = RoleStore("roles.json")
        store.delete(role.role_id)


@dataclass
class UserCredentials:
    """Stores the hashed password for a user.

    This object is never exposed outside ``Authentication`` and
    ``UserRepository``. Keeping credentials separate from ``User`` ensures
    that the user identity object is safe to pass to any layer without
    accidentally leaking sensitive data.
    """

    user_id: int
    password_hash: str


@dataclass
class Session:
    """Short-lived authenticated session."""

    session_token: str
    user_id: int
    roles: list[str]
    issued_at: datetime
    expires_at: datetime
    password: str = ""
    google_identity: "GoogleIdentity | None" = None

    def is_expired(self) -> bool:
        """Compare the expiry timestamp with the current UTC time."""
        now = datetime.now(timezone.utc) if self.expires_at.tzinfo else datetime.now()
        return now >= self.expires_at

    def decode_token(self) -> dict[str, Any]:
        """Validate and decode the session token into its claims."""
        if not self.session_token:
            return {}
        try:
            padded = self.session_token + "=" * (-len(self.session_token) % 4)
            payload = base64.urlsafe_b64decode(padded.encode("ascii"))
            claims = json.loads(payload.decode("utf-8"))
            if isinstance(claims, dict):
                return claims
        except Exception:
            pass
        return {
            "session_token": self.session_token,
            "user_id": self.user_id,
            "roles": self.roles,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def google_token(
        cls,
        token: str,
        *,
        audience: str | Sequence[str] | None = None,
        issuer: str | Sequence[str] | None = None,
        jwks: Mapping[str, Any] | None = None,
        jwks_url: str | None = None,
        verifier: "GoogleTokenVerifier | None" = None,
        user_resolver: Callable[["GoogleIdentity"], "User | None"] | None = None,
        session_token: str | None = None,
        lifetime: timedelta | None = None,
        leeway_seconds: int = 60,
        require_email_verified: bool = True,
    ) -> "Session":
        """Build a :class:`Session` from a verified Google (OIDC) ID token.

        The token is **verified**, not merely decoded: the RS256 signature is
        checked against the JWKS document, and the ``iss``, ``aud``, ``exp``
        and ``iat`` claims are validated before any claim is trusted. Only then
        is the identity mapped onto a local account.

        Args:
            token: the raw ID token (compact JWS string).
            audience: the OAuth client id(s) the token must be addressed to.
                Required unless ``verifier`` is supplied.
            issuer: acceptable ``iss`` value(s). Defaults to Google's issuers.
            jwks: an already-fetched JWKS document, so no network call is made.
            jwks_url: JWKS endpoint used when ``jwks`` is omitted.
            verifier: a pre-configured :class:`GoogleTokenVerifier`; when given,
                ``audience``/``issuer``/``jwks``/``jwks_url`` are ignored.
            user_resolver: maps the verified identity to a local ``User``. When
                it returns ``None``, a ``LookupError`` is raised rather than
                silently issuing a session for an unlinked account.
            session_token: reuse an existing token; a random one is generated
                otherwise.
            lifetime: requested session duration (default one hour). It is
                capped by the ID token's own expiry.
            leeway_seconds: clock-skew tolerance applied to time claims.
            require_email_verified: reject identities whose email is unverified.

        Returns:
            An unexpired :class:`Session`. If ``user_resolver`` was not supplied
            the session carries ``user_id=0`` and no roles; register the result
            with ``Authentication.adopt_session`` before using it for requests.

        Raises:
            GoogleTokenExpired: the token (or the resulting session) is expired.
            GoogleTokenInvalid: signature or claim validation failed.
            LookupError: the identity is valid but no local account is linked.
            ValueError: ``audience`` is missing, or ``lifetime`` is not positive.
        """
        from inventory.auth.google import GoogleTokenExpired, GoogleTokenVerifier

        if lifetime is not None and lifetime <= timedelta(0):
            raise ValueError("lifetime must be positive.")

        if verifier is None:
            if not audience:
                raise ValueError(
                    "audience is required when no verifier is supplied."
                )
            options: dict[str, Any] = {
                "leeway_seconds": leeway_seconds,
                "require_email_verified": require_email_verified,
            }
            if issuer is not None:
                options["issuers"] = (
                    (issuer,) if isinstance(issuer, str) else tuple(issuer)
                )
            if jwks is not None:
                options["jwks"] = jwks
            if jwks_url is not None:
                options["jwks_url"] = jwks_url
            verifier = GoogleTokenVerifier(audience, **options)

        identity = verifier.verify(token)

        user = user_resolver(identity) if user_resolver is not None else None
        if user_resolver is not None and user is None:
            raise LookupError(
                "No local account is linked to the Google identity "
                f"{identity.email or identity.subject}."
            )

        issued_at = datetime.now(timezone.utc)
        requested_lifetime = lifetime if lifetime is not None else timedelta(hours=1)
        expires_at = min(issued_at + requested_lifetime, identity.expires_at)
        if expires_at <= issued_at:
            raise GoogleTokenExpired(
                "The Google ID token expires too soon to start a session."
            )

        roles = (
            [str(user.role_id)]
            if user is not None and getattr(user, "role_id", 0)
            else []
        )
        return cls(
            session_token=session_token or secrets.token_urlsafe(32),
            user_id=user.user_id if user is not None else 0,
            roles=roles,
            issued_at=issued_at,
            expires_at=expires_at,
            password="",
            google_identity=identity,
        )

