import type { FastifyReply } from 'fastify'

/**
 * Prevent browser and intermediary caches from retaining authenticated data.
 * This is intentionally stronger than a generic private-cache directive because
 * Virexa responses can contain tenant-scoped operational and identity data.
 */
export function markSensitiveResponse(reply: FastifyReply): void {
  reply.header('Cache-Control', 'private, no-store, max-age=0')
  reply.header('Pragma', 'no-cache')
  reply.header('Expires', '0')
}
