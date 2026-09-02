import Fastify from 'fastify'
import cors from '@fastify/cors'
import cookie from '@fastify/cookie'
import helmet from '@fastify/helmet'
import { apiFailure, apiSuccess } from './contracts/http.js'
import type { LoginRequest, RegisterRequest } from './contracts/auth.js'

const app = Fastify({ logger: true, requestIdHeader: 'x-request-id', genReqId: () => crypto.randomUUID() })

await app.register(helmet, { contentSecurityPolicy: false })
await app.register(cookie)
await app.register(cors, {
  origin: process.env.FRONTEND_ORIGIN ?? 'http://localhost:3000',
  credentials: true,
})

app.get('/health', async (request) => apiSuccess({ status: 'ok' }, request.id))

app.post<{ Body: RegisterRequest }>('/api/v1/auth/register', async (request, reply) => {
  const { displayName, email, password, organizationName } = request.body
  if (!displayName || !email || !password || !organizationName) {
    return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Required registration fields are missing.', request.id))
  }
  if (password.length < 12) {
    return reply.code(400).send(apiFailure('WEAK_PASSWORD', 'Password does not meet the minimum policy.', request.id, { password: ['Use at least 12 characters.'] }))
  }
  // Persistence, password hashing, and session issuance will be added in the auth implementation slice.
  return reply.code(501).send(apiFailure('NOT_IMPLEMENTED', 'Registration service is not enabled yet.', request.id))
})

app.post<{ Body: LoginRequest }>('/api/v1/auth/login', async (request, reply) => {
  const { email, password } = request.body
  if (!email || !password) {
    return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Email and password are required.', request.id))
  }
  return reply.code(501).send(apiFailure('NOT_IMPLEMENTED', 'Authentication service is not enabled yet.', request.id))
})

app.get('/api/v1/auth/session', async (request, reply) => {
  return reply.code(401).send(apiFailure('UNAUTHENTICATED', 'Authentication is required.', request.id))
})

app.post('/api/v1/auth/logout', async (request, reply) => {
  reply.clearCookie('virexa_session', { path: '/' })
  return reply.code(204).send()
})

const port = Number(process.env.PORT ?? 4000)
await app.listen({ port, host: process.env.HOST ?? '0.0.0.0' })
