const LOCAL_FRONTEND_ORIGIN = 'http://localhost:3000'

export function frontendOrigin(environment: NodeJS.ProcessEnv = process.env): string {
  const configuredOrigin = environment.FRONTEND_ORIGIN?.trim()
  if (configuredOrigin) return configuredOrigin

  if (environment.NODE_ENV === 'production') {
    const error = new Error('FRONTEND_ORIGIN must be configured in production')
    error.name = 'INVALID_CONFIGURATION'
    throw error
  }

  return LOCAL_FRONTEND_ORIGIN
}

export function assertProductionConfiguration(environment: NodeJS.ProcessEnv = process.env): void {
  if (environment.NODE_ENV !== 'production') return

  const origin = frontendOrigin(environment)
  let parsedOrigin: URL
  try {
    parsedOrigin = new URL(origin)
  } catch {
    const error = new Error('FRONTEND_ORIGIN must be a valid absolute URL in production')
    error.name = 'INVALID_CONFIGURATION'
    throw error
  }

  if (parsedOrigin.protocol !== 'https:') {
    const error = new Error('FRONTEND_ORIGIN must use HTTPS in production')
    error.name = 'INVALID_CONFIGURATION'
    throw error
  }
}
