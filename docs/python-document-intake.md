# Python document quarantine intake candidate

Stacked on the opt-in FastAPI auth candidate. This is real bounded byte intake,
not OCR, scan validation, workflow execution, production rollout, or full Fastify
parity. Use the isolated Python database/migration chain only.

## Implemented HTTP contract

All routes use the existing `/api/v1` envelopes, server-derived tenant, live
session/role checks, and private no-store responses. Mutations require trusted
Origin. GET endpoints require document-read; intake requires document-management
(the current Python operator policy permits intake). Extraction management denies
operators, matching the existing Node route boundary.

- POST `/documents`: registers original filename, PDF/PNG/JPEG MIME type, declared
  byte size and SHA-256. Max 25 MiB, nonempty files. Original filenames are display
  metadata only, never storage paths. Existing camelCase frontend fields retained.
  Branch/department and non-upload sources return explicit unsupported errors;
  they are not silently accepted without authorization/hierarchy implementation.
- GET `/documents` and `/documents/{id}`: bounded cursor pages (1–100, default 50).
  Cursor is opaque and tenant/filter scoped, not an authorization credential.
- POST `/documents/{id}/uploads` with `Idempotency-Key`: creates or replays one
  durable immutable upload attempt per document. Keys cannot cross documents.
  An unfinished expired attempt can be renewed with the original key.
- PUT `/documents/{id}/uploads/{attemptId}/content`: actual streamed bytes to a
  private quarantine object. Requires active session, role, tenant and Origin;
  the returned URL is a same-host session-bound target, **not a public or signed
  bearer URL**. A target alone grants no access. No byte-download route exists.
- POST `/documents/{id}/uploads/{attemptId}/complete`: rechecks actual stored size,
  digest and signature; returns completed transport receipt, idempotently.
- POST `/documents/{id}/extractions`: returns DOCUMENT_NOT_READY until clean scan,
  then EXTRACTOR_UNAVAILABLE. Scanning remains disabled until explicitly configured;
  no extractor is configured or simulated.
  Extraction list returns unavailable until persistence exists, not invented jobs.

`scanStatus` is an additive response field: awaiting_upload -> pending_scan ->
clean/rejected, only through the optional internal local scanner.
Document status stays `received`. Upload `completed` means only verified transport;
it cannot imply safe content, successful extraction, validated fields or workflow
eligibility. See [local scanning](local-document-scanning.md) for the leased worker,
configuration prerequisites, failure behavior, and limitations. No API
accepts caller-supplied scan/result/status values.

## Local opt-in configuration

Set DOCUMENT_STORAGE_ROOT to a dedicated, absolute service-owned private directory
(mode 0700), outside any web/static serving root. Files are 0600. Set
PUBLIC_API_ORIGIN to the same explicit origin as the browser frontend, also in
ALLOWED_ORIGINS. HTTPS is required in production by the existing origin settings.
Default storage is disabled (503), so starting the API never silently stores files
in a public/repository directory. Storage is local single-host/single-process only;
do not share it across replicas or use ephemeral containers without durable disk.

The current frontend auth-only proxy deliberately blocks these routes. A separate
explicit document capability/proxy gate must forward exact routes and PUT bytes,
with cookie/Origin handling, before browser intake is enabled. Do not switch the
whole frontend to this candidate or expose the storage directory through that proxy.

Intake reserves at most 1,000 documents and 1 GiB of declared bytes per tenant,
serialized with a PostgreSQL organization row lock. At most four document mutation requests are
admitted per process; rejected requests do not acquire a DB connection. Total mutation
request deadline is 65 seconds, stream deadline 60 seconds. Metadata is capped at
32 KiB and 10 seconds. A 1 GiB free-disk reserve plus room for four maximum uploads
is enforced before writing; production still requires filesystem quotas, global
abuse/rate controls, monitored capacity, backups and an approved retention policy.
Header signatures only reject obvious MIME mismatch: they do not parse, sanitize,
scan for malware or prove document validity.

A private temporary file is exclusively created, hashed while streaming, fsynced,
and atomically hard-linked to its immutable opaque tenant/document/attempt key.
Validation failure removes the partial. A retry verifies an existing final object
rather than overwriting it, recovering a write followed by DB failure. Crash
partials older than 180 seconds can be recovered on retry, beyond the request
lifetime. No automatic deletion of completed quarantined data occurs; capacity
fails closed and an operator-approved retention process remains required.

## Extraction/provenance next boundary (design, not implemented)

A future job must reference tenant, document ID, immutable upload attempt/source
revision and verified SHA-256. A queued job requires a trusted clean scan receipt
for that exact source revision; a mutable document status alone is insufficient.
Persist provider name/version, model/config digest, schema version and source
reference server-side. Provider output is a new immutable result revision, never
an in-place update of earlier output. Canonical layout should retain page, reading
order, hierarchy/parent block ID, bounded text, table cells and page bounding boxes.
Every extracted field must point to source page/block/bounding-box and result
revision, with provider confidence distinguished from reviewer validation.

Review corrections create separate revisions with actor, time, source revision and
optimistic version; they cannot rewrite provider evidence. Completion/downstream
workflow eligibility requires successful schema validation plus reviewed required
fields on the latest revision and a still-valid scan. No status toggle constitutes
a workflow engine. Worker callbacks require service authentication plus composite
tenant/document/job ownership; browser users must not submit raw provider output.

Docling is the prospective canonical layout/provenance adapter. Unlimited-OCR and
PPStructureV3 are candidates for a future synthetic/consented corpus benchmark.
No models were installed, paid APIs called, client files transmitted, RAG implemented,
or extraction accuracy asserted in this milestone.

## Verification

`python -m pytest -q` exercises real SQLAlchemy persistence and real private-file
bytes with synthetic PDF-like fixtures; no extractor mock presents fake output.
The exact-head GitHub workflow additionally runs Alembic upgrades and the auth and
intake suites against PostgreSQL 16. SQLite is fast test coverage, not evidence
for PostgreSQL locking. Security cases include tenant/role revocation, missing
bytes, digest/size/MIME mismatch, bounded pagination, idempotency, immutable replay,
CSRF, deadlines/admission, stale-partial recovery, disk floor, and OpenAPI schemas.
