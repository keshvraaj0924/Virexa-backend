# Mandatory human layout review

This implements review of local layout candidates only. It does not extract a validated invoice, authorize accounting/payment/ERP execution, or make a workflow end-to-end approved. The Python extraction execution endpoint remains unavailable. No external model or new billing action is involved.

## Trust boundary

A successful parser job is an immutable machine candidate identified by its job UUID, source SHA-256, pipeline version and `layout-result-v1` schema. The schema tag is not a revision identity. Human changes are append-only events bound to that UUID and source/result-and-evidence hashes, with actor, time, revision and an allowlisted reason code. The original Docling JSON is never edited. Text corrections form an overlay and are never executable instructions or HTML.

The reviewer compares safe private source-page PNGs against candidate text. `sourceCompared: true` is their explicit attestation, not server proof they understood the document. The server additionally checks source previews, current private bytes, verified upload, clean scan, current candidate, fresh role and exact revision. An individual account administrator may review their own material. This is not a two-person approval policy.

Only `platform_admin`, `tenant_admin`, `organization_admin`, and `reviewer` may correct or decide. A dedicated reviewer receives document-read and review capability, not member administration or document upload capability. Existing organization administrators can assign that role. Role writers and review operations lock the same organization and reread actor authorization from the database; review also takes the session-owner lock shared with logout/revocation and revalidates the authenticated session after document locks and evidence checks; request/session role claims alone never authorize a decision.

## API

All responses use the existing success/error envelope. Reads are tenant-scoped. Mutations require trusted Origin and one Idempotency-Key of 16–255 characters.

- `GET /api/v1/layout-jobs/{jobId}/review`
- `POST /api/v1/layout-jobs/{jobId}/review/corrections`
- `POST /api/v1/layout-jobs/{jobId}/review/decisions`
- `GET /api/v1/layout-jobs/{jobId}/review/history?limit=25&cursor=3`
- `GET /api/v1/layout-review-events/{eventId}` resolves an audit resource ID to the same immutable event, job, document, revision and redacted decision metadata.

Review state contains `jobId`, `documentId`, `resultKind: layout`, `sourceSha256`, `revision`, `status`, cumulative `corrections`, `current`, `ready`, `eligible`, and `scope: layout_only`. Initial revision is zero. Initial status is `needs_review`. An accepted correction, approval or rejection increments revision once. Mutations return `{review, replayed}`. A repeated matching key returns the current authoritative state without reapplying the old action; it never approves intervening edits. Key reuse with a different request or actor fails.

Correction body:

```json
{"expectedRevision":0,"sourceSha256":"<64 lowercase hex>","resultKind":"layout","corrections":[{"nodeId":"#/texts/0","text":"Corrected text"}]}
```

Only existing canonical text-node `self_ref` IDs may be corrected. Maximum 100 nodes/request, 4,000 characters/text, 128 KiB normalized UTF-8 request, 256 KiB cumulative overlay, 1,000 cumulative corrected nodes and 1,000 review events/candidate. Empty text is an explicit deletion of that node's displayed text; arbitrary structural patches are not supported. Fields, node labels, bounding boxes and semantic invoice validation are not implemented.

Decision body:

```json
{"expectedRevision":1,"sourceSha256":"<64 lowercase hex>","resultKind":"layout","decision":"approve","reasonCode":"verified_against_source","sourceCompared":true}
```

Rejection may set `sourceCompared: false` when evidence is unavailable or unreadable; it still requires a current source hash, completed candidate, exact revision and authorized role. Missing evidence never permits approval or downstream eligibility. Rejection uses `decision: reject` and one of `incorrect_layout`, `incomplete_layout`, or `unreadable_source`. Free-form reasons are intentionally absent to avoid secret text in audit trails. There is no client `verified` flag or direct approved-status setter. No-op correction requests are rejected without creating a revision. A rejected revision cannot be approved directly. A reviewer must first create an explicit corrected revision, or review a new parser candidate. The rejection remains in history.

History uses a descending revision cursor with maximum 100 entries/page, actor/time/version/hash/reason and corrected-node count. It omits correction text and idempotency keys. Corrected text is visible only in the authenticated review snapshot, not general audit logs.

## Fail-closed consumption

`reviews.service.approved_snapshot(db, context, job_id, storage, result_kind="layout", expected_revision=N)` verifies eligibility afresh and returns a copied canonical layout with human text overlays plus immutable approval identity. The function deliberately does not commit. A future consumer must persist its intent in this same transaction while retaining the document lock, record the approval ID/source/result hashes (including the exact private preview bytes) and revision, and revalidate when consuming delayed work. A cached client `eligible` boolean is never authority.

Any edit resets status to `corrected`. Rejection blocks use. Creating a new parser job updates the server-owned current-candidate pointer under the document lock and invalidates the old approval even while pending. Wall-clock timestamps never decide currentness. Migration backfills only documents with one unambiguous existing candidate; ambiguous histories remain blocked. Changed source hashes/bytes, missing previews, unclean scans, missing source provenance, or stale revision fail closed. Parser enqueue and review serialize on the document lock. There is no reparse endpoint in the current parser; future versions must use that same lock and never overwrite candidate JSON.

The kind guard rejects invoice, accounting, payment and other semantic requests. No such consumer currently exists; do not describe the integration as ERP-ready. Candidate parser `reviewStatus: needs_review` remains immutable and is not authoritative for human review; the review API overlay is authoritative.

## Operational limits

Review history is append-only in service code, guarded against ORM update/delete and protected by a PostgreSQL immutable-history trigger installed by migration 0012. Database-owner privileges remain administrative trust. Keep the database service role minimally privileged. This migration adds no new credentials or persistent access.

Automated tests use synthetic candidate output and source bytes to verify policy and locking. They do not measure parser accuracy or prove a human read each page. Live private-document accuracy requires separate authorized evaluation.
