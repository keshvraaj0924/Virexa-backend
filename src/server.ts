import { Pool } from 'pg'
import { app } from './app.js'
import { AuditService } from './audit/service.js'
import { auditRoutes } from './audit/routes.js'
import { createAuthRepository } from './auth/repository.js'
import { PostgresOrganizationRepository } from './organization/repository.js'
import { organizationRoutes } from './organization/routes.js'

const port = Number(process.env.PORT ?? 4000)
const host = process.env.HOST ?? '0.0.0.0'

function databaseUrl(): string {
  const value = process.env.DATABASE_URL
  if (!value) {
    throw new Error('DATABASE_URL is not configured')
  }
  return value
}

// Organization administration is registered at the composition root so its
// dependencies are explicit and independently replaceable in tests. Tenant
// scope remains derived by organizationRoutes from the authenticated session.
const organizationAuthRepository = createAuthRepository(databaseUrl())
const organizationRepository = new PostgresOrganizationRepository(
  new Pool({ connectionString: databaseUrl(), max: 5 }),
)

await app.register(organizationRoutes, {
  authRepository: organizationAuthRepository,
  organizationRepository,
})

// Audit reads use an independent repository/service composition. The route
// derives organization scope from the authenticated session and never accepts
// a client-controlled tenant or organization identifier.
const auditAuthRepository = createAuthRepository(databaseUrl())
const auditService = new AuditService(
  new Pool({ connectionString: databaseUrl(), max: 5 }),
)

await app.register(auditRoutes, {
  authRepository: auditAuthRepository,
  auditService,
})

app.addHook('onClose', async () => {
  await Promise.all([
    organizationAuthRepository.close?.(),
    organizationRepository.close(),
    auditAuthRepository.close?.(),
    auditService.close(),
  ])
})

await app.listen({ port, host })

let shuttingDown = false

async function shutdown(signal: NodeJS.Signals): Promise<void> {
  if (shuttingDown) return
  shuttingDown = true
  app.log.info({ signal }, 'Graceful shutdown started')

  try {
    await app.close()
    app.log.info({ signal }, 'Graceful shutdown completed')
    process.exitCode = 0
  } catch (error) {
    app.log.error({ err: error, signal }, 'Graceful shutdown failed')
    process.exitCode = 1
  }
}

process.once('SIGTERM', () => {
  void shutdown('SIGTERM')
})
process.once('SIGINT', () => {
  void shutdown('SIGINT')
})
