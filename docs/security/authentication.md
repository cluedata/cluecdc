# Authentication

ClueCDC v0.2 uses local email-and-password accounts created through administrator
invites. It does not include SSO, OAuth, OIDC, LDAP, SAML, groups, or custom
roles.

## Bootstrap the first administrator

Apply the database migration, then run the interactive command from the API
environment:

```bash
python -m app.cli create-admin --email admin@example.com
```

The command securely prompts for the password and confirmation. For Compose:

```bash
docker compose exec cluecdc-api python -m app.cli create-admin --email admin@example.com
```

Passwords must contain at least 12 characters. ClueCDC stores an Argon2id hash,
never the password. No default account or password is created.

## Browser sessions

Sign in at `/login`. Successful authentication creates a random, server-side
session and sends only its opaque value in an `HttpOnly`, `SameSite=Lax` cookie.
Production cookies also use `Secure`. Sessions expire after
`SESSION_TTL_SECONDS` (eight hours by default); signing out deletes the stored
session immediately.

Disabled users cannot sign in, and disabling an account removes its current
sessions. Login failures use the same response for unknown emails and incorrect
passwords. A small per-process rate limit slows basic brute-force attempts.

## Production configuration

Set these values before starting the production API:

```env
AUTH_MODE=session
SESSION_SECRET=<at-least-32-random-characters>
SESSION_TTL_SECONDS=28800
INVITE_TTL_SECONDS=86400
PUBLIC_URL=https://cdc.example.com
```

Generate `SESSION_SECRET` with a cryptographically secure generator, for example
`openssl rand -hex 32`. Production startup rejects the documented development
value, secrets shorter than 32 characters, and non-session authentication.
`PUBLIC_URL` is the external browser origin used to construct invite links.

Keep TLS termination in front of the web application and keep the API on a
private network. Back up the metadata database because it contains users,
hashed sessions, hashed invitations, and audit history.

ClueCDC disables Uvicorn's raw access logger and redacts invite-token path
segments in its structured HTTP log. Configure the ingress or reverse proxy to
avoid recording `/invite/<token>` paths as well.
