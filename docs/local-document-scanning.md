# Optional local malware scanning

The Python API now creates one durable scan job atomically with a newly completed
upload. Scanning is **disabled by default**. No public endpoint can set a verdict.
Existing pending uploads are not silently backfilled. An operator must assess old
uploads before a future explicit backfill; this migration does not approve them.

Run `python -m app.domain.documents.scan_worker` only after explicitly configuring
`DOCUMENT_SCANNER_SOCKET` to an absolute local Unix socket, `DOCUMENT_STORAGE_ROOT`
to the same private quarantine directory as the API, and `DATABASE_URL` to
PostgreSQL. This is a single-host private-storage adapter. Do not run workers on a
host with a different quarantine directory. No remote socket/vendor URL is supported.
There is no new Python SDK dependency: the adapter uses Python's standard library.

Provision a supported ClamAV package from the official OS distribution, keep it
patched, and use FreshClam for current official databases. Run the daemon in UTC
(the VERSION protocol date has no timezone), protect its socket and parent
directory from other users, and give only the service identity access. No TCP port
is needed. Required daemon policy includes StreamMaxLength and MaxFileSize of at
least 25M, bounded MaxScanSize (100M), MaxScanTime (20000 milliseconds), MaxThreads
(4), AlertExceedsMax yes, AlertEncrypted yes, and PDF/archive scanning enabled.
Do not enable clean/verbose logging. These are deployment prerequisites; the client
cannot inspect daemon configuration through the scan protocol. The worker accepts
only a positive loaded database revision with a timestamp no older than 48 hours;
unavailable, stale, malformed, size-limit, and timeout responses cannot mark clean.

Global admission uses a PostgreSQL transaction advisory lock, four active leases,
and one active lease per tenant. Leases last 90 seconds; socket work has a total
30-second deadline; inputs are bounded to 25 MiB. Claims commit before I/O. Retries
wait 60/120 seconds and stop after three attempts, including crashed leases.
Exhaustion leaves the document pending_scan in private quarantine. Internal job
state/result codes identify the operational failure without storing raw daemon
replies. Future operator retry/recovery is deliberately not an HTTP feature.

Each job binds tenant, document, completed upload, and checksum. The worker hashes
the bounded private snapshot, submits that snapshot, checks the original again,
and compares file identity/metadata before and after reads. It applies the verdict only while the current lease and document hash match.
Rejected threats stay private with scanStatus rejected. Clean means only that the
configured engine returned no detected threat for those bytes. Malware scanning
is one defense, not a safety guarantee. It does not unlock downloads, extraction,
parsing, promotion, or OCR. Any future consumer must recheck the stored hash at use time; a clean verdict is not a permanent guarantee about a mutable filesystem. No OCR pages are billed. Scan audit records are
allowlisted, associated with the initiating actor, and contain only opaque IDs and
an outcome; filenames, contents, and antivirus signature names are excluded.

## Verification

`tests/test_document_scanning.py` tests protocol framing, strict reply handling,
stale signatures, fail-closed retries, hash integrity, lease expiry, and PostgreSQL
admission races. Its local protocol daemon is a test double, not malware-engine
evidence. `CLAMAV_TEST_SOCKET` opts into the separate genuine-engine clean/EICAR
smoke test. EICAR is the standard harmless antivirus test pattern, not real malware.
The CI workflow installs the Ubuntu 24.04 distribution engine and downloads current
official signatures; failure to download or load them fails the job rather than
fabricating health. It never scans customer files. Protocol tests normally use
Unix sockets; `SCAN_TEST_TCP_TRANSPORT=1` substitutes loopback TCP only inside the
test fixture for sandboxes denying AF_UNIX. Production remains Unix-socket-only.

Sources reviewed:
- https://docs.clamav.net/manual/Usage/ClamdProtocol.html
- https://docs.clamav.net/manual/Installing/Packages.html
- https://docs.clamav.net/manual/Usage/Configuration.html
- https://github.com/Cisco-Talos/clamav/blob/main/etc/clamd.conf.sample
