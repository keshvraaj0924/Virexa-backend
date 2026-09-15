# Trusted proxy configuration

Virexa authentication rate limits are keyed from Fastify's resolved `request.ip`. If the API is deployed behind a reverse proxy or load balancer, configure `TRUST_PROXY_HOPS` to the exact number of trusted proxy hops.

- Default: `0` (no proxy is trusted)
- Valid range: `0` through `10`
- Invalid or oversized values fail startup
- Set the value to the actual deployment topology; do not increase it merely to make forwarded headers work

For example, a single trusted ingress proxy should use:

```text
TRUST_PROXY_HOPS=1
```

This keeps the server-side authentication rate limiter tied to the client address resolved through the explicitly trusted proxy chain. Do not treat arbitrary `X-Forwarded-For` values as trusted client identity without configuring the proxy boundary.

The current rate-limit store remains process-local. `TRUST_PROXY_HOPS` improves client-IP resolution but does not turn the limiter into a fleet-wide quota. Multi-replica deployments still require a shared rate-limit store before relying on the limit as a global control.
