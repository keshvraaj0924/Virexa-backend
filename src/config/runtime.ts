const LOCAL_FRONTEND_ORIGIN = 'http://localhost:3000'

function invalidConfiguration(message: string): never {
  const error = new Error(message)
  error.name = 'INVALID_CONFIGURATION'
  throw error
}

export function frontendOrigin(environment: NodeJS.ProcessEnv = process.env): string {
  const configuredOrigin = environment.FRONTEND_ORIGIN?.trim()
  if (configuredOrigin) return configuredOrigin

  if (environment.NODE_ENV === 'production') {
    return invalidConfiguration('FRONTEND_ORIGIN must be configured in production')
  }

  return LOCAL_FRONTEND_ORIGIN
}

export function trustProxyHops(environment: NodeJS.ProcessEnv = process.env): number {
  const configured = environment.TRUST_PROXY_HOPS?.trim()
  if (!configured) return 0

  if (!/^\d+$/.test(configured)) {
    return invalidConfiguration('TRUST_PROXY_HOPS must be a non-negative integer')
  }

  const hops = Number(configured)
  if (!Number.isSafeInteger(hops) || hops > 10) {
    return invalidConfiguration('TRUST_PROXY_HOPS must be between 0 and 10')
  }

  return hops
}

export function assertProductionConfiguration(environment: NodeJS.ProcessEnv = process.env): void {
  if (environment.NODE_ENV !== 'production') return

  const origin = frontendOrigin(environment)
  let parsedOrigin: URL
  try {
    parsedOrigin = new URL(origin)
  } catch {
    return invalidConfiguration('FRONTEND_ORIGIN must be a valid absolute URL in production')
  }

  if (parsedOrigin.protocol !== 'https:') {
    return invalidConfiguration('FRONTEND_ORIGIN must use HTTPS in production')
  }

  trustProxyHops(environment)
}
