# Python identity migration parity

This branch aligns Python RBAC to the existing `/api/v1` role/permission contract.
The Fastify API remains authoritative until tenant isolation, session compatibility,
database migrations, integration tests and frontend parity all pass.

Do not enable Python authentication routes before resolving duplicate-email
ambiguity across organizations. Never select the first matching user from an
organization-unscoped query. Use a verified organization discriminator or
fail closed; do not expose a user-enumeration signal.
