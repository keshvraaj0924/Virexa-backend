# Optional private local PDF layout parsing

This is a bounded local **layout parser**, not semantic invoice extraction, a handwriting reader, an accuracy certificate, or an ERP integration. It is disabled by default. Successful output always requires human review. It does not approve accounting, payment, or document correctness, and does not unlock original-file downloads.

## Security and execution boundary

- A request must have the current `document:layout:request` capability. Operators cannot request parsing. Reads require `document:read`, with tenant scope checked on every request.
- The immutable upload must be completed and verified, with a matching durable clean scanner verdict for the same document, upload, and SHA-256. File identity and content hash are read again at admission, execution, publication, and result/source-preview access.
- A separate fixed-command subprocess receives verified bytes through stdin. The client cannot select a command, path, model, provider, or output. Docling remote services and external plugins are disabled, models are preprovisioned, and no request-time download is allowed.
- Linux resource limits, offline environment, network syscall denial, and filesystem restrictions fail closed. Deploy under a dedicated unprivileged account with application and model files read-only. These controls do not replace host/container hardening.
- A parser failure stays review-blocked. Exception text, file contents, and model logs are not included in public errors or audit metadata.
- Durable PostgreSQL leases admit at most two jobs globally and one per organization. A lease lasts 240 seconds; parsing has a 180-second wall deadline. A crashed lease can be reclaimed up to three attempts. A stale worker cannot publish. Explicit parser failures are terminal errors and cannot be force-completed by an HTTP request.

## Preview limits, not commercial pricing

One job per document and pipeline version; at most 100 lifetime layout jobs per organization for this preview release. These are execution admission caps, not paid-plan entitlements. Input: PDF only, maximum 25 MiB and 20 pages. Parser process memory: 6 GiB. Output protocol: 8 MiB including base64 source previews. Source PNGs: maximum 1 MiB each and 1000 pixels on the longest edge. Oversized, corrupt, encrypted, incomplete, or unsupported inputs fail closed. The worker does not charge OCR, token, or layout-page ledger usage; pageCount is the actual parsed page count.

## Optional local setup

The normal API installation does not import or install Docling. Configure `DOCUMENT_LAYOUT_ENABLED=true`, `DOCUMENT_LAYOUT_ASSETS=/absolute/private/read-only/model-root`, and the existing `DOCUMENT_STORAGE_ROOT` only after provisioning the optional pinned runtime and offline model integrity manifests. Use PostgreSQL for production lease admission.

Run the separate worker with:

    python -m app.domain.documents.layout_worker

Provision the optional runtime and assets in a trusted build environment before enabling the worker:

    python -m pip install -r tools/extraction_eval/requirements-docling-lock.txt --extra-index-url https://download.pytorch.org/whl/cpu
    python tools/extraction_eval/provision_layout_assets.py --output /absolute/private/model-root

The same interpreter must contain the optional pinned Docling dependencies. Model assets must match the reviewed Heron and TableFormer revisions and integrity manifest enforced in `layout_subprocess.py`. Do not generate trust manifests from unverified request data. Do not enable remote services, plugins, or runtime downloads to make missing-assets failures disappear. Hosts without required kernel isolation remain disabled.

## HTTP contract

All JSON responses use the existing `{data, meta: {requestId, timestamp}}` success envelope and standard error envelope. Existing authentication cookies, trusted Origin requirements for mutation, tenant scoping, and no-store policies apply.

- `POST /api/v1/documents/{documentId}/layout-jobs`, body `{ "pipelineVersion": "docling-local-v1" }` (or `{}`): 202 `{job, replayed}`. Repeated requests return the same version-bound job. No client idempotency key is needed because the database document/version key is canonical.
- `GET /api/v1/documents/{documentId}/layout-jobs`: `{items: LayoutStatus[]}`, newest first, maximum 20.
- `GET /api/v1/documents/{documentId}/layout-jobs/{jobId}`: `LayoutStatus`.
- `GET /api/v1/documents/{documentId}/layout-jobs/{jobId}/result`: immutable `LayoutResult`, only once a current source-bound result exists.
- `GET /api/v1/documents/{documentId}/layout-jobs/{jobId}/pages/{pageNumber}`: 1-based bounded `image/png` source evidence. The same source, tenant, and scan checks apply. No raw PDF, HTML, arbitrary render request, or remote URL is returned.

`LayoutStatus`: id, documentId, state (`pending`, `running`, `needs_review`, `error`), pipelineVersion, sourceSha256, pageCount (nullable), failureCode (nullable), createdAt, updatedAt.

`LayoutResult`: resultVersion (`layout-result-v1`), reviewStatus (`needs_review`), sourceSha256, pipelineVersion, parserVersion, modelVersion, pageCount, sourcePreviewAvailable, warnings, canonical. The canonical Docling representation preserves page metadata, reading-order tree and references, text provenance/bounding boxes, and tables. Embedded image data is stripped; separate source previews provide visual evidence. Treat all extracted text, captions, and links as untrusted data. Render as text, never injected HTML or instructions.

`LayoutJob.id` identifies this immutable candidate. A separate human-review subsystem must version corrections and decisions without rewriting canonical output. Its decision is distinct from the candidate's permanent initial `reviewStatus`. A candidate is never automatically approved.

Important failures: 503 `LAYOUT_UNAVAILABLE`; 409 `DOCUMENT_NOT_READY`, `LAYOUT_SOURCE_CHANGED`, `LAYOUT_RESULT_UNAVAILABLE`, `LAYOUT_PREVIEW_QUOTA`; 415 `LAYOUT_PDF_REQUIRED`; scoped 404 for unknown or other-tenant resources. Unsupported or additional body fields fail validation. There are no public scan-verdict, job completion, retry, or approval endpoints in this module.

## Evidence and limitations

Job/API tests use explicitly mocked parsers and synthetic documents, and do not establish Docling recognition quality. The optional adapter tests cover protocol bounds and isolation; genuine engine smoke is separately labeled. No private sample documents, extracted contents, or benchmark results belong in the repository.

OCR is disabled in this baseline. Scanned pages can provide source visual evidence and layout regions, but scanned or handwritten text recognition is not supported or promised. Native-text identifiers and table associations can still be missed. Users must compare the source image and provenance before relying on any parsed content. Semantic field mapping, RAG, and downstream ERP execution remain outside this milestone.


The required `Isolated local layout engine` CI workflow runs the production adapter unchanged on a generated synthetic PDF, asserting real canonical text and PNG evidence. It fails rather than skipping when explicitly enabled assets or Linux isolation are unavailable. The optional local test skips only when no smoke-assets path was configured. This development host lacks Landlock (`ENOSYS`), so local mocked tests or unsandboxed conversion diagnostics are not successful isolated runtime evidence. Check the exact commit's required hosted smoke result before activation.

### Host containment prerequisites

Landlock ABI 3 or newer is required; older ABI versions leave truncation unmediated and are rejected. The service must run as a dedicated non-root UID. The subprocess caps the UID's processes/threads at 64 and denies separate-process creation, cross-process memory/descriptor access and signals, and filesystem ownership/permission/timestamp/xattr mutations. Host administrators must additionally bound the parser's temporary filesystem size/inode count and apply service-level memory/PID limits (for example a dedicated quota-controlled temporary volume and cgroup). `RLIMIT_FSIZE` limits each file, not total scratch usage; no application claim of a full VM/container sandbox or total-host DoS isolation is made. Do not enable the worker on a shared privileged service account or without these host limits.

Source PNGs are validated before storage and on every serve: only bounded RGB/RGBA 8-bit noninterlaced PNGs, valid chunk CRC/order, exact bounded decompression, and no metadata chunks or trailing bytes. The API performs no native image decoding or PDF rendering.

The recursively readable Python runtime prefixes, fixed parser module directory, and model asset root must contain no service credentials, secrets, or tenant files. Temporary storage must be dedicated to the parser rather than shared writable service storage.
