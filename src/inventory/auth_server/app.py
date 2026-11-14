"""A standalone OAuth 2.0 / OpenID Connect provider with Google-shaped endpoints.

Demo only: ``login_hint`` plus auto-approve skips authentication, so anyone who
can reach the service can mint a token for any demo account. It is a small
identity provider ("mini Google") that issues RS256-signed ID tokens. Point an OAuth client at it exactly as you would at Google:

    authorization  http://127.0.0.1:5001/o/oauth2/v2/auth
    token          http://127.0.0.1:5001/token
    userinfo       http://127.0.0.1:5001/oauth2/v2/userinfo
    JWKS           http://127.0.0.1:5001/oauth2/v3/certs
    discovery      http://127.0.0.1:5001/.well-known/openid-configuration

Run it with ``python -m inventory.auth_server`` or the ``inventory-auth-server``
console script. The in-memory store and the ephemeral signing key make it a
development/demo service; see ``docs/GOOGLE_OIDC.md`` for the production gaps.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import dataclass, replace
import os
import secrets
import sys
import time
import urllib.parse
from collections.abc import Sequence

from flask import Flask, Response, jsonify, redirect, render_template_string, request

from inventory.auth_server.config import (
    AuthServerConfig,
    DEFAULT_ISSUER,
    OAuthClient,
    SUPPORTED_CODE_CHALLENGE_METHODS,
    SUPPORTED_SCOPES,
)
from inventory.auth_server.errors import OAuthError
from inventory.auth_server.keys import (
    SigningKey,
    generate_signing_key,
    jwks_document,
    load_signing_key,
)
from inventory.auth_server.store import InMemoryAuthorizationStore, verify_pkce
from inventory.auth_server.tokens import (
    TokenError,
    build_access_token,
    build_id_token,
    decode_access_token,
)
from inventory.auth_server.users import DemoUser, DemoUserDirectory


PAGE_STYLE = """
    :root { color-scheme: light; }
    * { box-sizing: border-box; }
    body {
        margin: 0; min-height: 100vh; display: grid; place-items: center;
        font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
        background: #0f172a; color: #e2e8f0;
    }
    .card {
        width: min(560px, calc(100vw - 32px)); background: #1e293b;
        border: 1px solid #334155; border-radius: 18px; padding: 32px;
        box-shadow: 0 24px 60px rgba(2, 6, 23, 0.55);
    }
    .brand { display: flex; align-items: center; gap: 10px; margin-bottom: 22px; }
    .dot { width: 12px; height: 12px; border-radius: 50%; background: #38bdf8; }
    .brand span { letter-spacing: 0.14em; font-size: 12px; color: #94a3b8; text-transform: uppercase; }
    h1 { font-size: 22px; margin: 0 0 8px; }
    p { color: #94a3b8; line-height: 1.55; margin: 0 0 18px; }
    .client { color: #e2e8f0; font-weight: 700; }
    .scope { display: inline-block; margin: 0 6px 6px 0; padding: 4px 10px;
             border-radius: 999px; background: #0f172a; border: 1px solid #334155;
             font-size: 12px; color: #cbd5f5; }
    label.user { display: flex; align-items: center; gap: 10px; padding: 12px;
                 border: 1px solid #334155; border-radius: 12px; margin-bottom: 10px;
                 cursor: pointer; }
    label.user:hover { border-color: #38bdf8; }
    label.user input { accent-color: #38bdf8; }
    .meta { display: flex; flex-direction: column; }
    .meta small { color: #94a3b8; }
    .actions { display: flex; gap: 10px; margin-top: 22px; }
    button { flex: 1; padding: 12px 16px; border-radius: 10px; border: 1px solid #334155;
             font-size: 14px; font-weight: 600; cursor: pointer; }
    button.primary { background: #38bdf8; border-color: #38bdf8; color: #082f49; }
    button.secondary { background: transparent; color: #cbd5f5; }
    code { background: #0f172a; padding: 2px 6px; border-radius: 6px; color: #7dd3fc; }
    ul { padding-left: 18px; color: #94a3b8; line-height: 1.7; }
"""

CONSENT_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Sign in with SMOभर ID</title>
    <style>{{ style }}</style>
</head>
<body>
    <main class="card">
        <div class="brand"><span class="dot"></span><span>SMOभर Identity</span></div>
        <h1>Choose an account</h1>
        <p>
            <span class="client">{{ client.name or client.client_id }}</span> wants to sign you in
            and access:
        </p>
        <div>
            {% for scope in scopes %}<span class="scope">{{ scope }}</span>{% endfor %}
        </div>
        <form method="post" action="{{ consent_endpoint }}">
            {% for name, value in hidden.items() %}
            <input type="hidden" name="{{ name }}" value="{{ value }}">
            {% endfor %}
            {% for user in users %}
            <label class="user">
                <input type="radio" name="email" value="{{ user.email }}"
                       {% if loop.first %}checked{% endif %} required>
                <span class="meta">
                    <strong>{{ user.name }}</strong>
                    <small>{{ user.email }}</small>
                </span>
            </label>
            {% endfor %}
            <div class="actions">
                <button class="secondary" type="submit" name="decision" value="deny">Cancel</button>
                <button class="primary" type="submit" name="decision" value="allow">Allow access</button>
            </div>
        </form>
    </main>
</body>
</html>
"""

ERROR_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ title }}</title>
    <style>{{ style }}</style>
</head>
<body>
    <main class="card">
        <div class="brand"><span class="dot"></span><span>SMOभर Identity</span></div>
        <h1>{{ title }}</h1>
        <p>{{ detail }}</p>
        {% if hint %}<p><code>{{ hint }}</code></p>{% endif %}
    </main>
</body>
</html>
"""

INDEX_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>SMOभर Identity Provider</title>
    <style>{{ style }}</style>
</head>
<body>
    <main class="card">
        <div class="brand"><span class="dot"></span><span>SMOभर Identity</span></div>
        <h1>OAuth 2.0 / OpenID Connect provider</h1>
        <p>Issuer <code>{{ issuer }}</code>. Discovery document at
           <code>/.well-known/openid-configuration</code>.</p>
        <ul>
            <li>Authorize: <code>{{ authorize }}</code></li>
            <li>Token: <code>{{ token }}</code></li>
            <li>Userinfo: <code>{{ userinfo }}</code></li>
            <li>JWKS: <code>{{ jwks }}</code></li>
        </ul>
        <p>Demo accounts:</p>
        <ul>
            {% for user in users %}<li><code>{{ user.email }}</code> — {{ user.name }}</li>{% endfor %}
        </ul>
    </main>
</body>
</html>
"""


@dataclass
class AuthorizationRequest:
    """A parsed authorization request, shared by GET and consent POST."""

    redirect_uri: str
    scopes: list[str]
    state: str
    nonce: str | None
    code_challenge: str | None
    code_challenge_method: str | None
    login_hint: str
    access_type: str
    prompt: str


def _parse_scope(raw: str | None, default: str = "") -> list[str]:
    """Split a space-delimited scope string, preserving order and de-duplicating."""
    values = (raw or default).replace(",", " ").split()
    return list(dict.fromkeys(value for value in values if value))


def _redirect_with(redirect_uri: str, params: dict[str, str]) -> Response:
    """Append query parameters to a redirect URI that may already have a query."""
    filtered = {key: value for key, value in params.items() if value}
    query = urllib.parse.urlencode(filtered)
    if not query:
        return redirect(redirect_uri, code=302)
    separator = "&" if urllib.parse.urlparse(redirect_uri).query else "?"
    response = redirect(f"{redirect_uri}{separator}{query}", code=302)
    response.headers["Cache-Control"] = "no-store"
    return response


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def create_auth_server(
    config: AuthServerConfig | None = None,
    *,
    signing_key: SigningKey | None = None,
    users: DemoUserDirectory | None = None,
) -> Flask:
    """Build the provider application.

    ``signing_key`` and ``users`` are injectable so tests can pin a key and an
    account set instead of relying on the process defaults.
    """
    cfg = config or AuthServerConfig.from_environment()
    key = signing_key or generate_signing_key()
    directory = users or DemoUserDirectory()
    store = InMemoryAuthorizationStore(
        code_ttl_seconds=cfg.authorization_code_ttl_seconds,
        refresh_ttl_seconds=cfg.refresh_token_ttl_seconds,
    )

    app = Flask(__name__, static_folder=None)
    app.extensions["auth_server"] = {
        "config": cfg,
        "key": key,
        "store": store,
        "users": directory,
    }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def error_page(status: int, title: str, detail: str, hint: str = "") -> Response:
        body = render_template_string(
            ERROR_TEMPLATE,
            style=PAGE_STYLE,
            title=title,
            detail=detail,
            hint=hint,
        )
        return Response(body, status=status, mimetype="text/html")

    def redirect_error(
        request_obj: AuthorizationRequest,
        error: str,
        description: str,
    ) -> Response:
        return _redirect_with(
            request_obj.redirect_uri,
            {"error": error, "error_description": description, "state": request_obj.state},
        )

    def read_authorization_request(source) -> AuthorizationRequest:
        return AuthorizationRequest(
            redirect_uri=(source.get("redirect_uri") or "").strip(),
            scopes=_parse_scope(source.get("scope"), cfg.default_scope),
            state=(source.get("state") or "").strip(),
            nonce=(source.get("nonce") or "").strip() or None,
            code_challenge=(source.get("code_challenge") or "").strip() or None,
            code_challenge_method=(source.get("code_challenge_method") or "").strip() or None,
            login_hint=(source.get("login_hint") or "").strip(),
            access_type=(source.get("access_type") or "online").strip().lower(),
            prompt=(source.get("prompt") or "").strip().lower(),
        )

    def validate_authorization_request(
        request_obj: AuthorizationRequest,
        source,
    ) -> tuple[OAuthClient | None, Response | None]:
        """Validate in RFC 6749 order; return the client or a failure response."""
        client_id = (source.get("client_id") or "").strip()
        client = cfg.clients.get(client_id)
        if client is None:
            # Never redirect to an unverified URI.
            return None, error_page(
                400,
                "Unknown client",
                "This authorization request names a client that is not registered.",
                client_id or "client_id is required",
            )
        if not client.allows_redirect_uri(request_obj.redirect_uri):
            return None, error_page(
                400,
                "Invalid redirect_uri",
                "The redirect_uri must match one registered for this client, exactly.",
                request_obj.redirect_uri or "redirect_uri is required",
            )

        if (source.get("response_type") or "") != "code":
            return None, redirect_error(
                request_obj,
                "unsupported_response_type",
                "Only the authorization code flow is supported.",
            )

        unknown = [scope for scope in request_obj.scopes if scope not in SUPPORTED_SCOPES]
        if unknown:
            return None, redirect_error(
                request_obj,
                "invalid_scope",
                f"Unsupported scope(s): {', '.join(unknown)}.",
            )

        if request_obj.code_challenge:
            method = request_obj.code_challenge_method or "plain"
            request_obj.code_challenge_method = method
            if method not in SUPPORTED_CODE_CHALLENGE_METHODS:
                return None, redirect_error(
                    request_obj,
                    "invalid_request",
                    "code_challenge_method must be S256 or plain.",
                )
        elif cfg.require_pkce_for_public_clients and client.is_public:
            return None, redirect_error(
                request_obj,
                "invalid_request",
                "Public clients must use PKCE with code_challenge_method=S256.",
            )

        if request_obj.access_type not in {"online", "offline"}:
            return None, redirect_error(
                request_obj,
                "invalid_request",
                "access_type must be online or offline.",
            )
        return client, None

    def issue_code_redirect(
        client: OAuthClient, request_obj: AuthorizationRequest, user: DemoUser
    ) -> Response:
        code = store.issue_code(
            client_id=client.client_id,
            redirect_uri=request_obj.redirect_uri,
            subject=user.subject,
            scope=" ".join(request_obj.scopes),
            nonce=request_obj.nonce,
            code_challenge=request_obj.code_challenge,
            code_challenge_method=request_obj.code_challenge_method,
            access_type=request_obj.access_type,
        )
        return _redirect_with(
            request_obj.redirect_uri, {"code": code, "state": request_obj.state}
        )

    def consent_page(
        client: OAuthClient, request_obj: AuthorizationRequest, source
    ) -> Response:
        hidden = {
            name: source.get(name) or ""
            for name in (
                "client_id",
                "redirect_uri",
                "response_type",
                "scope",
                "state",
                "nonce",
                "code_challenge",
                "code_challenge_method",
                "access_type",
                "login_hint",
            )
        }
        body = render_template_string(
            CONSENT_TEMPLATE,
            style=PAGE_STYLE,
            client=client,
            scopes=request_obj.scopes,
            users=directory.all(),
            hidden=hidden,
            consent_endpoint="/oauth2/auth/consent",
        )
        return Response(body, mimetype="text/html")

    def authenticate_client() -> OAuthClient:
        """Client authentication via HTTP Basic or ``client_secret_post``."""
        header = request.headers.get("Authorization", "")
        client_secret: str | None = None
        if header.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(
                    header.split(" ", 1)[1].strip(), validate=True
                ).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, IndexError, ValueError) as error:
                raise OAuthError(
                    "invalid_client",
                    "Malformed Authorization header.",
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="token"'},
                ) from error
            raw_id, _, raw_secret = decoded.partition(":")
            client_id = urllib.parse.unquote(raw_id)
            client_secret = urllib.parse.unquote(raw_secret) or None
        else:
            client_id = (request.form.get("client_id") or "").strip()
            client_secret = request.form.get("client_secret") or None

        client = cfg.clients.get(client_id)
        if client is None:
            raise OAuthError(
                "invalid_client",
                "Unknown client.",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="token"'},
            )
        if client.client_secret:
            if not client_secret or not secrets.compare_digest(
                client_secret, client.client_secret
            ):
                raise OAuthError(
                    "invalid_client",
                    "Client authentication failed.",
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="token"'},
                )
        elif client_secret:
            raise OAuthError(
                "invalid_client",
                "A public client must not send a client secret.",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="token"'},
            )
        return client

    def token_bundle(
        client: OAuthClient,
        *,
        subject: str,
        scope: str,
        nonce: str | None = None,
    ) -> dict[str, object]:
        user = directory.find_by_subject(subject)
        if user is None:
            raise OAuthError("invalid_grant", "The authenticated account no longer exists.")

        issued_at = int(time.time())
        access_expires = issued_at + cfg.access_token_ttl_seconds
        access_token = build_access_token(
            key=key,
            issuer=cfg.issuer,
            client_id=client.client_id,
            subject=user.subject,
            scope=scope,
            issued_at=issued_at,
            expires_at=access_expires,
        )
        payload: dict[str, object] = {
            "access_token": access_token,
            "expires_in": cfg.access_token_ttl_seconds,
            "token_type": "Bearer",
            "scope": scope,
        }
        if "openid" in scope.split():
            payload["id_token"] = build_id_token(
                key=key,
                issuer=cfg.issuer,
                audience=client.client_id,
                subject=user.subject,
                email=user.email,
                email_verified=user.email_verified,
                name=user.name,
                picture=user.picture,
                issued_at=issued_at,
                expires_at=issued_at + cfg.id_token_ttl_seconds,
                nonce=nonce,
                access_token=access_token,
            )
        return payload

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discovery_document() -> dict[str, object]:
        return {
            "issuer": cfg.issuer,
            "authorization_endpoint": cfg.authorization_endpoint,
            "token_endpoint": cfg.token_endpoint,
            "userinfo_endpoint": cfg.userinfo_endpoint,
            "jwks_uri": cfg.jwks_uri,
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": sorted(SUPPORTED_SCOPES),
            "token_endpoint_auth_methods_supported": [
                "client_secret_basic",
                "client_secret_post",
                "none",
            ],
            "code_challenge_methods_supported": list(SUPPORTED_CODE_CHALLENGE_METHODS),
            "claims_supported": [
                "iss",
                "aud",
                "sub",
                "email",
                "email_verified",
                "name",
                "picture",
                "iat",
                "exp",
                "nonce",
            ],
        }

    @app.get("/.well-known/openid-configuration")
    @app.get("/.well-known/oauth-authorization-server")
    def discovery() -> Response:
        return _no_store(jsonify(discovery_document()))

    # ------------------------------------------------------------------
    # Authorization endpoint
    # ------------------------------------------------------------------

    @app.get("/o/oauth2/v2/auth")
    @app.get("/oauth2/auth")
    def authorize() -> Response:
        request_obj = read_authorization_request(request.args)
        client, problem = validate_authorization_request(request_obj, request.args)
        if problem is not None:
            return problem

        user = directory.find_by_email(request_obj.login_hint)
        if request_obj.prompt == "none":
            if user is None:
                return redirect_error(
                    request_obj,
                    "login_required",
                    "prompt=none was requested but no account is signed in.",
                )
            return issue_code_redirect(client, request_obj, user)

        if cfg.auto_approve and user is not None:
            return issue_code_redirect(client, request_obj, user)
        return consent_page(client, request_obj, request.args)

    @app.post("/oauth2/auth/consent")
    def consent() -> Response:
        request_obj = read_authorization_request(request.form)
        client, problem = validate_authorization_request(request_obj, request.form)
        if problem is not None:
            return problem

        if (request.form.get("decision") or "").lower() != "allow":
            return redirect_error(
                request_obj, "access_denied", "The resource owner denied the request."
            )

        user = directory.find_by_email(request.form.get("email"))
        if user is None:
            return error_page(
                400,
                "Choose an account",
                "Select one of the demo accounts to continue.",
            )
        return issue_code_redirect(client, request_obj, user)

    # ------------------------------------------------------------------
    # Token endpoint
    # ------------------------------------------------------------------

    @app.post("/token")
    @app.post("/oauth2/token")
    def token_endpoint() -> Response:
        try:
            client = authenticate_client()
            grant_type = (request.form.get("grant_type") or "").strip()

            if grant_type == "authorization_code":
                code = (request.form.get("code") or "").strip()
                if not code:
                    raise OAuthError("invalid_request", "code is required.")
                record = store.consume_code(
                    code,
                    client.client_id,
                    (request.form.get("redirect_uri") or "").strip(),
                )
                if not verify_pkce(
                    record.code_challenge,
                    record.code_challenge_method,
                    request.form.get("code_verifier"),
                ):
                    raise OAuthError("invalid_grant", "PKCE verification failed.")

                payload = token_bundle(
                    client, subject=record.subject, scope=record.scope, nonce=record.nonce
                )
                if record.access_type == "offline":
                    payload["refresh_token"] = store.issue_refresh_token(
                        client_id=client.client_id,
                        subject=record.subject,
                        scope=record.scope,
                    )
            elif grant_type == "refresh_token":
                raw_token = (request.form.get("refresh_token") or "").strip()
                if not raw_token:
                    raise OAuthError("invalid_request", "refresh_token is required.")
                record = store.get_refresh_token(raw_token, client.client_id)
                payload = token_bundle(client, subject=record.subject, scope=record.scope)
                payload["refresh_token"] = raw_token
            else:
                raise OAuthError(
                    "unsupported_grant_type",
                    "Supported grant types: authorization_code, refresh_token.",
                )
        except OAuthError as error:
            response = jsonify(
                {"error": error.error, "error_description": error.description}
            )
            response.status_code = error.status_code
            for name, value in error.headers.items():
                response.headers[name] = value
            return _no_store(response)

        return _no_store(jsonify(payload))

    # ------------------------------------------------------------------
    # Userinfo and JWKS
    # ------------------------------------------------------------------

    @app.get("/oauth2/v2/userinfo")
    @app.get("/userinfo")
    def userinfo() -> Response:
        scheme, _, raw_token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not raw_token:
            return _bearer_error("invalid_token", "Provide a Bearer access token.")
        try:
            claims = decode_access_token(raw_token, key, cfg.issuer)
        except TokenError as error:
            return _bearer_error("invalid_token", str(error))

        user = directory.find_by_subject(str(claims.get("sub", "")))
        if user is None:
            return _bearer_error("invalid_token", "The token subject is unknown.")
        return _no_store(
            jsonify(
                {
                    "sub": user.subject,
                    "email": user.email,
                    "email_verified": user.email_verified,
                    "name": user.name,
                    "picture": user.picture,
                }
            )
        )

    def _bearer_error(error: str, description: str) -> Response:
        response = jsonify({"error": error, "error_description": description})
        response.status_code = 401
        response.headers["WWW-Authenticate"] = f'Bearer error="{error}"'
        return _no_store(response)

    @app.get("/oauth2/v3/certs")
    @app.get("/jwks")
    def jwks() -> Response:
        response = jsonify(jwks_document(key))
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    # ------------------------------------------------------------------
    # Operational endpoints
    # ------------------------------------------------------------------

    @app.get("/health")
    def health() -> Response:
        return jsonify(
            {
                "status": "ok",
                "issuer": cfg.issuer,
                "kid": key.kid,
                "store": store.stats(),
            }
        )

    @app.get("/")
    def index() -> Response:
        body = render_template_string(
            INDEX_TEMPLATE,
            style=PAGE_STYLE,
            issuer=cfg.issuer,
            authorize=cfg.authorization_endpoint,
            token=cfg.token_endpoint,
            userinfo=cfg.userinfo_endpoint,
            jwks=cfg.jwks_uri,
            users=directory.all(),
        )
        return Response(body, mimetype="text/html")

    return app


def main(argv: Sequence[str] | None = None) -> None:
    """Run the provider with the Flask development server."""
    parser = argparse.ArgumentParser(
        description="OAuth 2.0 / OpenID Connect provider for SMOभर Inventory"
    )
    parser.add_argument("--host", default=os.environ.get("AUTH_SERVER_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("AUTH_SERVER_PORT", "5001"))
    )
    parser.add_argument(
        "--issuer",
        default=os.environ.get("AUTH_SERVER_ISSUER", ""),
        help="Public issuer URL; defaults to http://<host>:<port>.",
    )
    parser.add_argument(
        "--key-file",
        default=os.environ.get("AUTH_SERVER_KEY_FILE", ""),
        help="PEM RSA private key; an ephemeral key is generated when omitted.",
    )
    parser.add_argument(
        "--no-auto-approve",
        action="store_true",
        help="Always render the consent screen instead of signing in login_hint.",
    )
    args = parser.parse_args(argv)

    config = AuthServerConfig.from_environment()
    if args.issuer.strip():
        issuer = args.issuer.strip()
    elif config.issuer != DEFAULT_ISSUER:
        issuer = config.issuer
    else:
        issuer = f"http://{args.host}:{args.port}"
    config = replace(config, issuer=issuer)
    if args.no_auto_approve:
        config = replace(config, auto_approve=False)

    if args.key_file:
        key = load_signing_key(args.key_file)
    else:
        key = generate_signing_key()
        print(
            "[auth-server] No --key-file supplied: using an ephemeral RSA key. "
            "Tokens are invalid after restart.",
            file=sys.stderr,
        )

    app = create_auth_server(config, signing_key=key)
    print(f"[auth-server] issuer {config.issuer}", file=sys.stderr)
    for client in config.clients.values():
        print(
            f"[auth-server] client {client.client_id} "
            f"({'public + PKCE' if client.is_public else 'confidential'}) "
            f"redirects={list(client.redirect_uris)}",
            file=sys.stderr,
        )
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":  # pragma: no cover
    main()
