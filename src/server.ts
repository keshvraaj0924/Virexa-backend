import { app } from './app.js'

const port = Number(process.env.PORT ?? 4000)
const host = process.env.HOST ?? '0.0.0.0'

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
