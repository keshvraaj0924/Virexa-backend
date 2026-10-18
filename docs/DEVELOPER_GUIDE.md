# Run the paired Python candidate locally

This is the current developer entry point for the separate backend and frontend
repositories. Earlier milestone guides describe historical slices; their old pins,
auth-only gates and legacy registration examples are not the current setup.
This candidate is for isolated local evaluation, not production deployment.

## 1. Prerequisites and exact source pair

Use Git, Python 3.12, Node.js 22 with npm, and a running PostgreSQL 16 server
with `psql` available. These versions match integration CI. Python dependencies
still resolve from ranges; an immutable source pin does not lock dependencies.
Redis is not needed for this local slice. Do not start the legacy Fastify server
or run its SQL migrations against the Python database.

Windows: from PowerShell run `wsl --status` and `wsl --list --verbose` to check
for an existing WSL2 Linux distribution, then enter it with `wsl`. Run all the
commands below inside that Linux shell, with repositories and private storage
in its Linux home directory (not `/mnt/c`). If WSL2 is absent, install/configure
it through your normal approved Windows setup first. The quarantine adapter uses
POSIX ownership, `os.getuid()` and `O_NOFOLLOW`; native Windows backend intake is
not supported. Linux commands were checked against source and CI; this guide does
not claim a Windows/WSL end-to-end run. Browser loopback forwarding into WSL must
also work on your machine; keep the exact public hostname below.

For a new checkout (skip clone if already cloned):

```sh
git clone https://github.com/keshvraaj0924/Virexa-backend.git
git clone https://github.com/keshvraaj0924/Virexa-Frontend.git
git -C Virexa-backend fetch origin feat/python-workflow-records
git -C Virexa-backend checkout --detach 7e406c4f8013e3f3224ff62dce4bc0123d05a12c
git -C Virexa-Frontend fetch origin feat/python-workflow-records
git -C Virexa-Frontend checkout --detach 9c33460f472ee8e3a2c4aeea5507de67a1b75c37
```

These are runtime pins, not a request to merge draft branches. The paired
`docs/python-candidate-developer-guide` branches add this guide and local example
files to those runtime trees. To use the included examples, fetch and check out
that docs branch in each repository instead:

```sh
git -C Virexa-backend fetch origin docs/python-candidate-developer-guide
git -C Virexa-backend checkout docs/python-candidate-developer-guide
git -C Virexa-Frontend fetch origin docs/python-candidate-developer-guide
git -C Virexa-Frontend checkout docs/python-candidate-developer-guide
```

Do not discard existing local edits.
All seven real frontend integration workflows use the same backend runtime pin.

## 2. Create an isolated database and private local directories

Use a new database and a local-only login. With your PostgreSQL administrator
connection in `psql`, run these statements interactively. `\password` prompts
without putting a password in shell history; choose a local-only value yourself.

```sql
CREATE ROLE virexa_local LOGIN;
\password virexa_local
CREATE DATABASE virexa_python_local OWNER virexa_local;
```

If either name already exists, inspect it and choose unused names. Do not drop,
reset or reuse a customer, Node-managed or shared database. Update the example URL
with your actual database host, port, name and URL-encoded local password.

```sh
install -d -m 700 "$HOME/.local/share/virexa-local/quarantine"
install -d -m 700 "$HOME/.local/share/virexa-local/mail"
cd Virexa-backend
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
cp docs/local-backend.env.example .env
chmod 600 .env
```

Edit `.env` before continuing. Replace both placeholders: the database URL and
absolute quarantine directory. Dotenv does not expand `$HOME` as a shell does;
use your actual absolute path. Keep it outside repositories and web-served paths.
The example is explicitly `ENVIRONMENT=test` for synthetic loopback email capture;
it must never be deployed. No production secrets or SMTP credentials are needed.
Do not copy the root legacy Node `.env.example` for the Python candidate.

```sh
python -m alembic upgrade head
python -m alembic check
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 4000 --no-access-log
```

Migrations are explicit; HTTP startup does not create tables. Expected migration
head is `0008_workflow_records`. Keep Uvicorn in the foreground in this terminal.
In a second backend terminal activate `.venv` and run the existing local sink:

```sh
python tools/synthetic_signup_smtp.py 1025 "$HOME/.local/share/virexa-local/mail"
```

Keep it in the foreground. It accepts synthetic addresses only and writes private
capture files; it never delivers to external inboxes. Use `person@example.com`
with a distinct local part for each new account. `.test`/`.invalid` addresses can
fail the API's email validation even though the capture sink accepts them.

## 3. Start Next.js on the same browser host

In a third terminal, from `Virexa-Frontend`:

```sh
cp docs/local-frontend.env.example .env.local
npm ci
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Open exactly `http://127.0.0.1:3000`. Do not switch between `localhost` and
`127.0.0.1`: cookie host and trusted Origin must agree. The Next same-host route
allowlist proxies browser `/api/v1` requests to backend port 4000. SSR uses the
server-only `AUTH_API_ORIGIN` and forwards the incoming session cookie. The backend
`PUBLIC_API_ORIGIN` is the frontend browser origin, not the backend listener.
Never expose `AUTH_API_ORIGIN` or credentials via a `NEXT_PUBLIC_` variable.

The frontend example explicitly enables authentication, quarantine intake,
sessions, usage, audit and workflow records. Flags select implemented surfaces;
they do not grant permission. Apply flags at both build and server runtime.
For a production-mode *local build*, use `npm run build` then
`npm run start -- --hostname 127.0.0.1 --port 3000` instead of `dev`; this is still
a test backend, not production validation. Restart/rebuild after changing flags.

Check `http://127.0.0.1:4000/health` and `/ready`; readiness queries PostgreSQL.
Register an Individual or Organization account. Organization requires a name;
Individual receives its own isolated workspace. Each normalized email currently
belongs to one workspace; the account-kind selector does not create membership
or authorize tenant access. No domain-based organization inference is performed.
Read the latest local JSON capture file privately, copy its verification code
into the UI, then choose a password. Verification alone creates no session;
completion sets an HttpOnly cookie. Captures contain short-lived proof secrets:
do not commit, screenshot, upload or share them; remove only your disposable
captures when finished. Stop each foreground service with Ctrl+C.

## 4. What the candidate actually supports

All API paths below are under `/api/v1` with standard `{data, meta}` success and
`{error, meta}` failure envelopes. Writes require a trusted Origin. Session cookies
are HttpOnly/SameSite=Lax and responses carrying identity or tenant data are
private/no-store. Capabilities come from `GET /me/permissions`; role labels are
presentation data, not a replacement for server authorization.

- Auth: `/auth/signup/start`, `/verify`, `/complete` (the latter two under
  `/auth/signup`), `/auth/login`, `/auth/session`, `/auth/logout`, `/me`.
  Legacy `/auth/register` returns 410 by default. Do not enable its test bypass
  for manual signup; older regression fixtures opt in only on isolated databases.
- Documents: metadata, bounded private upload and completion/list flows. Uploaded
  files stay `pending_scan`; signature checks are not malware scanning. No OCR,
  model extraction, public download, promotion or customer document upload is part
  of this local test. Use synthetic files only.
- Sessions: current user's inventory and authorized revocation. Resource-state
  and tenant checks apply even when a capability is present.
- Usage: personal meters/history and capability-gated organization usage;
  entitlements are read-only preview state. No paid billing, payment setup,
  invented model-token/OCR metrics or workflow execution metering.
- Audit: tenant-only forward events at `/audit/events`, filtered and paginated.
  This is bounded implemented-event coverage, not historical reconstruction,
  immutable retention or compliance certification.
- Workflows: `/workflows` list/create and `/{id}` read/versioned patch. Names,
  descriptions and lifecycle status are stored records. `active` does not execute
  anything. No runner, step/action JSON, integration connection, delete or AI call.
  Creation uses an idempotency key; conflicts require reopening current state.
  After uncertain creation, retry unchanged within the mounted page, or check the
  list before creating again after navigation. No automatic write retries.

Missing/malformed capabilities fail closed. The backend additionally checks
workspace, ownership, observed version and resource state. Neither a platform role
nor a client-supplied tenant selector bypasses tenant isolation. Unsupported user
management, analytics and integrations must not be inferred from legacy Node code.

## 5. Code map and maintenance conventions

Backend: `app/main.py` assembles FastAPI; `app/core/config.py` validates settings;
`app/core/policy.py` and `app/domain/auth/permissions.py` govern effective access;
`app/domain/{auth,documents,usage,audit,workflows}` hold domain schemas, routes and
services; `alembic/versions` holds the separate Python migration chain. The
legacy TypeScript `src/` and SQL `migrations/` are a separate runtime.

Frontend: `src/lib/auth/proxy.ts` is the bounded same-host proxy;
`src/lib/auth/server.ts` handles SSR identity; `src/contracts` validates API shapes;
`src/components/access` handles effective access; `src/app/app` contains protected
pages and feature gates. `src/lib/api` centralizes API requests and errors.

Use descriptive domain names, small cohesive functions, explicit types at
boundaries and validated request/response schemas. Follow PEP 8 naming and
readability in Python; current Ruff configuration uses a 100-character line
limit and a selected rule set, not blanket proof of PEP 8 compliance. Keep domain
policy separate from transport/UI logic. Use typed, redacted public errors and
correlation IDs; never expose SQL, credentials, cookies, email proofs, document
contents or provider exceptions. Catch only errors you can handle; do not swallow
authorization failures or turn unknown data into successful empty/fake results.

Browser tests: scope alert assertions to the named form or feedback region.
An unscoped `page.getByRole('alert')` also matches Next's route announcer. Give
selects unique accessible labels and await completed Next navigation before
pressing Back. Test interrupted/repeated interactions and uncertain writes.

## 6. Validation and troubleshooting

From the activated backend environment:

```sh
python -m ruff check app
python -m pytest -q
```

The ordinary test suite uses isolated fixtures; PostgreSQL race/locking cases
require their dedicated test URLs. Never point `TEST_DATABASE_URL`,
`DOCUMENT_TEST_DATABASE_URL` or `SIGNUP_TEST_DATABASE_URL` at the manual local
workspace above or any valuable database: those fixtures recreate tables. For the
full PostgreSQL matrix and exact lint file list, use
`.github/workflows/python-foundation.yml` as the executable specification with a
separate disposable database. A SQLite pass is not PostgreSQL concurrency proof.

From the frontend:

```sh
npm run lint
npm run typecheck
npm test
npm run build
```

`npm run test:e2e` is the UI-only suite, not full real-backend evidence. Real
browser suites are `playwright.auth.config.ts`, `playwright.signup.config.ts`,
`playwright.intake.config.ts`, `playwright.sessions.config.ts`,
`playwright.usage.config.ts`, `playwright.audit.config.ts` and
`playwright.workflows.config.ts`; invoke using `npx playwright test --config FILE`.
Install Chromium with `npx playwright install chromium` first. Their matching
`.github/workflows` files define distinct fixture flags and synthetic DB/SMTP
setup. Use separate disposable services, not your manually created workspace.
Do not enable legacy registration merely to make a manual signup work.

- Readiness fails: check PostgreSQL connectivity and the asyncpg URL; confirm the
  correct database was migrated rather than resetting it.
- Signup returns 503: the loopback SMTP sink must be running on port 1025 and the
  backend must use test/local_smtp mode. Real email delivery is not configured.
- Login loops or origin rejection: use exactly 127.0.0.1:3000 everywhere; check
  proxy/public origins and cookie host. Do not weaken Origin checks.
- Quarantine startup fails: use an absolute Linux service-owned 0700 directory,
  not a symlink, Windows-mounted path or web directory. Keep sufficient disk space.
- Page missing/read-only: check its build/runtime flag and server capabilities;
  don't bypass authorization or infer availability from a role name.

Documentation-only changes do not constitute a new runtime test pass. The backend
Python PR workflow has path filters; the frontend's PR workflows do not. A docs
branch publication alone runs none of their branch-restricted push workflows.
Current CI evidence must always be associated with its exact runtime commit.
Production work still needs reviewed schema/data cutover, dependency locking,
HTTPS/cookie/proxy validation, distributed rate limiting, durable email delivery
and retention operations, private storage lifecycle and deployment review.
