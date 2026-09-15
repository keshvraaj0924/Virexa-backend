# Virexa Backend Architecture

Virexa adopts the attached enterprise blueprint as its architectural reference, adapted to Virexa's operations-automation domain. The blueprint emphasizes async API boundaries, hierarchical tenancy, granular RBAC/ABAC, background execution, agent orchestration, secure document/RAG access, connectors, observability, and centralized exception handling.

## Virexa target architecture

```text
Client
  -> Middleware (request-id, auth/session, security, logging, metrics)
  -> API v1 / GraphQL
  -> Domain Services
  -> Repositories / Unit of Work
  -> PostgreSQL

                    +--> Task Queue / Redis --> Workers
                    +--> Agent Runtime / LangGraph
                    +--> Document & RAG pipeline
                    +--> Connector framework
                    +--> Audit / telemetry
```

## Tenant hierarchy

`Tenant -> Organization -> Branch -> Department -> Persona -> User`

Every protected resource must carry tenant ownership and an explicit scope. Authorization is enforced server-side; frontend visibility is never treated as authorization.

## Access control

RBAC provides role-to-permission grants. ABAC evaluates contextual attributes such as tenant, organization, branch, department, persona, resource owner, classification, and action. Parent settings may cascade while lower scopes can explicitly override supported settings.

## Security model

- Passwords are hashed with a modern password KDF; plaintext passwords are never stored.
- Short-lived access sessions and rotating refresh sessions are server-verifiable and revocable.
- Browser sessions use Secure, HttpOnly cookies where applicable.
- CSRF protection is required for cookie-authenticated state-changing requests.
- Secrets are configuration/infrastructure concerns, never committed to source.
- Every authorization decision is made on the server.
- Authentication, authorization, validation, domain, and infrastructure failures map to stable API errors with correlation IDs.

## API contract

REST remains the primary integration surface under `/api/v1`. GraphQL is an optional query surface for clients that benefit from aggregation. OpenAPI is generated from FastAPI schemas and is the source for typed frontend client generation.

Standard response shape:

```json
{"data": {}, "meta": {"request_id": "..."}}
```

Standard error shape:

```json
{"error": {"code": "...", "message": "...", "details": []}, "meta": {"request_id": "..."}}
```

## Domain direction

The backend will grow around Virexa capabilities rather than copying unrelated Mikaila domain names. Initial bounded contexts are:

- Identity & Access
- Tenancy & Organization
- Users / Personas / Roles / Permissions
- Workflow & Automation
- Documents & Ingestion
- AI Agents / Agent Runs
- Connectors & Integrations
- Analytics / Operational Metrics
- Audit & Compliance
- Notifications

## Agent architecture

AI workflows use explicit state schemas, modular nodes, deterministic validation boundaries, tool adapters, and persisted run metadata. Agents never bypass authorization or directly mutate tenant data without domain services.

## Background processing

Heavy or retryable work such as document ingestion, indexing, connector synchronization, report generation, and long-running agent jobs runs outside the request lifecycle through a durable queue/worker architecture.

## Observability

All requests receive correlation IDs. Metrics cover request count, latency, error rates, queue health, worker health, and domain activity. Health endpoints distinguish liveness/readiness from dependency health. Structured logs must not leak credentials, tokens, or sensitive document contents.

## Engineering rule

No placeholder auth, fake credentials, hidden authorization, or UI-only security. Features are implemented end-to-end: schema -> migration -> repository -> service -> authorization -> API -> tests -> frontend integration.