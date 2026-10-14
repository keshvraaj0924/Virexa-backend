/**
 * Detect only the configured @fastify/rate-limit response for this request.
 *
 * The rate-limit plugin propagates errorResponseBuilder's API envelope to
 * Fastify's error handler as a plain object. Unknown exceptions must not
 * accidentally be translated into a client-visible 429 response.
 */
export function isRateLimitEnvelope(error: unknown, requestId: string): boolean {
  if (typeof error !== 'object' || error === null || !('error' in error) || !('meta' in error)) return false
  const detail = error.error
  const meta = error.meta
  return typeof detail === 'object'
    && detail !== null
    && 'code' in detail
    && detail.code === 'RATE_LIMITED'
    && 'requestId' in detail
    && detail.requestId === requestId
    && typeof meta === 'object'
    && meta !== null
    && 'requestId' in meta
    && meta.requestId === requestId
}
