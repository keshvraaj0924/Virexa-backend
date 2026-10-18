# Virexa Backend integration candidate

One runnable review candidate pairing the FastAPI backend and Next.js frontend.
This is a draft for isolated local evaluation; it is not production-ready.

Start with [the paired developer guide](docs/DEVELOPER_GUIDE.md): prerequisites,
exact source pins, isolated PostgreSQL setup, private environment examples,
synthetic verification email, API contracts, feature flags, run/test commands and
known limitations. Windows backend evaluation requires WSL2/Linux with private
storage inside its Linux filesystem; native Windows intake is unsupported.

## Implemented scope

| Area | Available in this candidate | Boundary |
| --- | --- | --- |
| Accounts | Verified Individual/Organization signup, login/logout, sessions | One workspace per email; real provider delivery unconfigured |
| Access | Server capabilities, tenant/ownership checks, live role resolution | No client-side or platform-role tenant bypass |
| Documents | Private bounded quarantine intake and status | No public download, extraction, OCR or promotion |
| Scanning | Optional local ClamAV worker, durable leases, clean/rejected status | Disabled by default; clean is not a safety guarantee |
| Usage | Personal/organization meters and entitlements preview | No actual billing or invented AI/OCR metrics |
| Audit | Supported tenant-only forward events and pagination | No complete historical/compliance guarantee |
| Workflows | Versioned tracking records and lifecycle metadata | No runner, integrations or AI execution |
| Team | Existing-member roster and role controls | No invites/provisioning; organization only; last-admin protection |

The design still needs user acceptance. All evaluation must use synthetic accounts
and files. Never upload private documents, SMTP credentials, cookies, verification
proofs or customer samples to this public repository.

## Review and validation

All existing milestone branches and PRs remain unchanged. The integration branch
targets `main` only for review; it does not merge or deploy anything. CI checks exact
PR heads. The nine real frontend browser workflows hard-pin the matching backend.
Backend CI separately covers PostgreSQL races/isolation and genuine ClamAV with
fresh official definitions; frontend scan tests exercise the real lease worker
with a deterministic protocol fixture, not a substitute malware engine.

Check the exact integration PR head's CI before relying on it. Source-milestone
passes are not an integration-head pass. Current CI definitions are executable
validation instructions; [the guide](docs/DEVELOPER_GUIDE.md) explains which tests
need disposable databases and local engine services.

## Code and historical material

`app/` and `alembic/` are the Python runtime and migration chain. `src/` and
`migrations/` remain the separate legacy TypeScript backend; do not mix databases
or run the Node migration chain against Python data.

Earlier setup and architectural context is preserved in
[the historical README](docs/LEGACY_README.md). It contains legacy contracts and
milestone pins; use the current developer guide instead for this paired candidate.
Production requires separate reviewed data/schema cutover, dependency locking,
HTTPS/cookie/proxy review, durable email, distributed limits, storage operations
and deployment decisions. Offline extraction evaluation does not enable an OCR
pipeline; optional PyMuPDF license review remains a prerequisite to product use.

Human layout review API and safety boundary: [human-review.md](docs/human-review.md).
