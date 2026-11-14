# Google-style OpenID Connect provider

> **Demo only.** Anyone who can reach this service can obtain an ID token for
> any demo account: `login_hint` plus auto-approve skips authentication
> entirely. Never point a production client at it, and never map its accounts
> onto real inventory users outside a demo.

A second, standalone Flask application that acts as the OAuth 2.0 / OpenID
Connect provider for the inventory UI. It issues Google-shaped RS256 ID tokens,
so the "Sign in with Google" button can run a real authorization-code flow
without a Google Cloud project.

The provider is deliberately separate from the inventory app:

```text
browser ──▶ inventory app ──▶ auth_server (this app)     identity assertion
                    │                    │
                    └──── Session.google_token ◀── ID token (RS256, JWKS)
```

The provider says **who** the user is. The inventory app decides **what** that
user may do, by mapping the verified email onto a local `users` row and its
role.

## Run it

```powershell
# from the repository root
python -m pip install -e ".[oauth]"
$env:PYTHONPATH = "src"
python -m inventory.auth_server --port 5001
```

Or, after an editable install, `inventory-auth-server --port 5001`.

The service prints its issuer, client registration and a warning when it
generates an ephemeral signing key. A PEM key can be supplied with
`--key-file`; without it every restart invalidates all issued tokens.

## Run the inventory app against it

The inventory app already contains the client half of the flow:

| Route | Purpose |
| --- | --- |
| `GET /api/auth/google/start` | creates `state` and a PKCE verifier, then redirects to the provider |
| `GET /api/auth/google/callback` | exchanges the code, verifies the ID token, adopts a session |

```powershell
# terminal 1 — identity provider
python -m inventory.auth_server --port 5001

# terminal 2 — inventory app
python -m inventory.app.main --mode server --port 5000
```

Open `http://127.0.0.1:5000/login` and click **Sign in with Google**.

Configuration lives in `.env` (see `.env.example`): `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_ISSUER`, `GOOGLE_REDIRECT_URI`,
`GOOGLE_AUTO_PROVISION`, `GOOGLE_DEFAULT_ROLE_ID`.

The callback redirects to `/login#session_token=…` (or `#error=…`). The login
page stores the token under `smobhar_session_token` and `token`, clears the
fragment with `history.replaceState`, and lets the existing role routing send
the user to `/admin`, `/workspace1` or `/home`.

Account linking: an identity whose email matches an existing `users` row keeps
that row's role. An unknown identity is provisioned with
`GOOGLE_DEFAULT_ROLE_ID` (3 = Supervisor, VIEW only) when auto-provisioning is
on; set `GOOGLE_AUTO_PROVISION=0` to require an admin-created account instead.
An ID token never grants a role the database does not already assign.

## Endpoints

| Purpose | Path |
| --- | --- |
| Discovery (OIDC) | `/.well-known/openid-configuration` |
| Discovery (RFC 8414) | `/.well-known/oauth-authorization-server` |
| Authorization | `/o/oauth2/v2/auth` (alias `/oauth2/auth`) |
| Consent submission | `POST /oauth2/auth/consent` |
| Token | `POST /token` (alias `/oauth2/token`) |
| Userinfo | `/oauth2/v2/userinfo` (alias `/userinfo`) |
| JWKS | `/oauth2/v3/certs` (alias `/jwks`) |
| Health | `/health` |

The paths mirror Google's, so a client written against Google works unchanged
once its issuer and endpoints point here.

## Demo accounts

| Email | Name | Subject |
| --- | --- | --- |
| `demo@smobhar.local` | Demo User | `google-oauth2\|1001` |
| `manager@smobhar.local` | Inventory Manager | `google-oauth2\|1002` |

With `AUTH_SERVER_AUTO_APPROVE=1` (the default), an authorization request that
carries `login_hint=<email>` issues a code immediately. Without a hint — or with
auto-approve disabled — the consent screen lists the accounts.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `AUTH_SERVER_HOST` | `127.0.0.1` | Bind address |
| `AUTH_SERVER_PORT` | `5001` | Bind port |
| `AUTH_SERVER_ISSUER` | `http://127.0.0.1:5001` | `iss` claim and discovery base |
| `AUTH_SERVER_CLIENT_ID` | `smobhar-inventory` | Registered client |
| `AUTH_SERVER_CLIENT_SECRET` | *(empty)* | Empty makes the client public and PKCE mandatory |
| `AUTH_SERVER_REDIRECT_URI` | `http://127.0.0.1:5000/api/auth/google/callback` | Comma-separated allow-list |
| `AUTH_SERVER_AUTO_APPROVE` | `1` | Skip the consent screen when `login_hint` is known |
| `AUTH_SERVER_REQUIRE_PKCE` | `1` | Reject public clients without PKCE |
| `AUTH_SERVER_ID_TOKEN_TTL` | `3600` | ID token lifetime (seconds) |
| `AUTH_SERVER_ACCESS_TOKEN_TTL` | `3600` | Access token lifetime |
| `AUTH_SERVER_REFRESH_TOKEN_TTL` | `86400` | Refresh token lifetime |
| `AUTH_SERVER_CODE_TTL` | `300` | Authorization code lifetime |
| `AUTH_SERVER_KEY_FILE` | *(empty)* | PEM RSA private key; ephemeral when unset |

## Consuming an ID token

`Session.google_token` verifies the token before trusting any claim:

```python
from inventory.auth.models import Session
from inventory.auth.google import GoogleTokenInvalid

def resolve(identity):
    """Map a verified Google identity onto a local account."""
    return user_repository.find_by_email(identity.email)  # None if unlinked

try:
    session = Session.google_token(
        id_token,
        audience="smobhar-inventory",
        issuer="http://127.0.0.1:5001",   # omit for real Google
        jwks=requests.get(jwks_uri).json(),  # or pass jwks_url and let it fetch
        user_resolver=resolve,
    )
except GoogleTokenInvalid:
    abort(401, description="The Google token could not be verified.")

services.authentication.adopt_session(session)   # make it usable by /api/*
```

What is checked, in order:

1. the JWT header's `alg` is RS256 (a token with `alg: none` or `HS256` is
   rejected before any signature work — no key-confusion path);
2. the signature validates against the JWKS key named by `kid`;
3. `iss` is one of the accepted issuers, `aud` contains this client id,
   `exp`/`iat` are within the clock-skew leeway;
4. `sub` is present, and `email` is present and verified unless
   `require_email_verified=False`;
5. only then is `user_resolver` consulted. A `None` result raises `LookupError`
   rather than minting a session for an unlinked account.

The resulting session's lifetime is the shorter of the requested `lifetime`
(default one hour) and the ID token's own expiry, so a long-lived session can
never outlive the assertion it was built from.

`Session.google_token` does not register the session with `Authentication`.
That is intentional — `adopt_session` is the explicit step that makes a session
acceptable to `require_session` in the HTTP API.

## Protocol behaviour

- Authorization code flow only; `response_type=token` is refused.
- PKCE (`S256` or `plain`) supported; public clients must use `S256`.
- Authorization codes are single-use, short-lived, and bound to the client and
  the exact `redirect_uri`.
- Client authentication via HTTP Basic or `client_secret_post`; public clients
  must not send a secret.
- Unknown `client_id` or unregistered `redirect_uri` produces a local `400`,
  never a redirect (RFC 6749 §4.1.2.1).
- Errors after a valid redirect are returned as `error`/`error_description`
  with the original `state`.
- Token responses carry `Cache-Control: no-store`.
- `access_type=offline` issues a refresh token; refresh does not rotate it.
- Access tokens are RS256 JWTs with `token_use=access`, which keeps userinfo
  stateless and makes an ID token unusable there.

## Limitations

This is a development/demo identity service. Before production:

- **State is in memory.** Authorization codes and refresh tokens are lost on
  restart, so it cannot run as more than one process.
- **The signing key is ephemeral** unless `AUTH_SERVER_KEY_FILE` is set. Publish
  a stable key and rotate it deliberately.
- **No account management.** The demo directory is hard-coded; there is no
  registration, password, MFA or consent history.
- **No revocation endpoint** and no `prompt=consent` / `max_age` handling.
- **No dynamic client registration**, no rate limiting on the token endpoint.
- The consent screen is a demo picker, not an authenticated login.

## Tests

```powershell
python -m pytest tests/test_auth_server.py -q
```

46 tests cover discovery, the full code flow, PKCE success/failure, code replay,
client authentication, refresh, userinfo, claim and signature rejection
(tampered signature, wrong issuer/audience, expired, `alg=none`, HMAC
confusion, unknown `kid`, unverified email), session capping, and
`adopt_session`.
