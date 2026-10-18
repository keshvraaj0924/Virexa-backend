# Email-verified individual and organization signup (Python candidate)

This draft adds two workspace kinds without changing the established tenant boundary. An individual gets their own isolated workspace; an organization gets its own isolated workspace. The existing `organization_id` foreign key remains the authoritative tenant ID in every session, document, permission and usage request. A selector is never authorization. This is not multi-workspace membership: the email remains globally unique and each user belongs to one workspace. No email-domain tenant matching or organization-email field is used.

## Wire contract

All paths use `/api/v1`, trusted Origin, existing error/success envelopes and no-store responses.

1. `POST /auth/signup/start`: `{accountType: "individual" | "organization", displayName, email, organizationName?}`. Organization name is required only for organization and rejected for individual. Returns 202 `{challengeId, expiresInSeconds: 600, resendAfterSeconds: 60, message}`. Repeating this call after the 60-second cooldown resends and invalidates the earlier challenge and any setup token. Existing-account responses have the same shape, and a code is still delivered to that mailbox. There is no account existence disclosure to someone who does not control the mailbox.
2. `POST /auth/signup/verify`: `{challengeId, code}`. The code is 32 URL-safe characters with 192 bits of randomness; support paste and do not use a six-digit widget. Returns `{setupToken, expiresInSeconds}`. Code is consumed once. Five wrong attempts exhaust the challenge.
3. `POST /auth/signup/complete`: `{challengeId, setupToken, password}`. Creates the user, separate workspace and authenticated session atomically, returns 201 with the established session response/cookie. Password policy is 12–128 characters and rejects whitespace padding below the minimum, low-diversity/repeated patterns, all-numeric passwords, and common-password examples. Long passphrases are supported without arbitrary uppercase/symbol rules. Password is salted Argon2id, not reversibly encrypted. No reusable password is emailed.

Verification does not authenticate or create a user. The setup token cannot access normal APIs, does not outlive the original ten-minute window, and is single-use. Replaying a code, stale setup proof, expired or exhausted challenge gives `400 SIGNUP_PROOF_INVALID`. Duplicate-account completion also gives this generic error. Unknown fields, including `verified` or client role/tenant assertions, are rejected. `429 SIGNUP_COOLDOWN` returns `Retry-After: 60`; the existing process-local IP guard returns `RATE_LIMITED`.

`POST /auth/login` accepts optional `accountType`. A mismatch returns generic `INVALID_CREDENTIALS` and no session. Existing clients omitting it remain compatible. Authenticated user payloads in login, complete, `/auth/session` and `/me` include server-owned `accountType`.

## Email delivery and safe operation

Delivery defaults to disabled: `503 EMAIL_DELIVERY_UNAVAILABLE`, no challenge, account or session. Production rollout requires an operator-configured SMTP provider and verified sender; none was configured and no real mail was sent for this milestone.

- `SIGNUP_EMAIL_DELIVERY=smtp`
- `SIGNUP_SMTP_HOST`, `SIGNUP_SMTP_PORT` (default 587), `SIGNUP_SMTP_SENDER`
- Optional paired `SIGNUP_SMTP_USERNAME` and `SIGNUP_SMTP_PASSWORD` from the deployment's secret manager, never a committed `.env` or browser variable.

The production adapter requires STARTTLS with certificate validation before credentials or message transmission. Authentication/delivery refusal rolls back the challenge and returns a redacted 503. There is no durable outbox or claim of inbox delivery: a successful start means the configured SMTP server accepted the message and the challenge committed. A crash/commit failure after SMTP acceptance can leave an unusable emailed code; retry starts a new proof. Never log codes, proofs, passwords, provider credentials, request bodies or SMTP exceptions. Existing request errors redact SQL/provider details.

For disposable synthetic tests only: `ENVIRONMENT=test SIGNUP_EMAIL_DELIVERY=capture` exposes an in-memory sink to Python fixtures, or `SIGNUP_EMAIL_DELIVERY=local_smtp SIGNUP_SMTP_PORT=1025` sends only to the local loopback sink. Run `python tools/synthetic_signup_smtp.py 1025 /private/temporary/captures`. The harness accepts only synthetic example domains and writes private 0600 JSON files in a 0700 directory, keyed by SHA256(lowercase email). Delete disposable captures after testing; never include them in screenshots, logs or published artifacts. Both adapters are rejected outside explicit test mode.

## Migration and rollback boundaries

Apply additive migration `0006_verified_signup` after usage ledger `0005`. Existing workspaces backfill to organization; no user is moved, merged, reset or newly asserted to have verified their email. Existing password logins continue. Do not mark legacy accounts as verified or mass-reset passwords as part of this rollout.

Legacy `/auth/register` returns 410 by default in every environment. `ALLOW_LEGACY_REGISTRATION=true` is accepted only with `ENVIRONMENT=test`, for isolated old regression fixtures; production/development startup rejects that bypass. Do not deploy the TypeScript legacy auth server as an alternate signup route: this feature belongs to the opt-in Python candidate and needs an explicit service cutover review.

Production requires PostgreSQL for row locks/advisory email locks; SQLite tests are not concurrency evidence. Per-email cooldown and attempt counters are DB-backed. The existing per-IP guard is process-local, so a shared edge/Redis limiter is still a production prerequisite for replicas. SMTP calls are timeout-bounded but synchronous within the transaction. An operator retention job for expired challenge PII and provider bounce handling remain rollout work, not silently enabled background behavior.

## Verification

Tests cover both kinds, account selector mismatch, no pre-verification sessions, password hashing, origin/input validation, redaction, delivery rollback, expiry, resend invalidation, exhausted attempts, duplicate accounts, tenant isolation, and PostgreSQL same-code/same-proof/concurrent-start/attempt races with post-lock freshness. Existing auth, documents, session, permission and usage suites remain regression gates. Live SMTP delivery is intentionally not verified.
