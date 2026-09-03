import type { FastifyRequest } from 'fastify'

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

export function assertTrustedOrigin(request: FastifyRequest): void {
  if (SAFE_METHODS.has(request.method)) return

  const configuredOrigin = process.env.FRONTEND_ORIGIN
  const origin = request.headers.origin
  if (!configuredOrigin || !origin || origin !== configuredOrigin) {
    const error = new Error('Untrusted request origin')
    error.name = 'UNTRUSTED_ORIGIN'
    throw error
  }
}
