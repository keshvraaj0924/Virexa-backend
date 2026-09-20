import type { S3DocumentStorageConfig } from './s3-storage.js'

const DEFAULT_UPLOAD_TTL_SECONDS = 10 * 60
const MAX_UPLOAD_TTL_SECONDS = 15 * 60

/**
 * Reads document object-storage configuration without ever accepting static
 * credentials. AWS credentials remain the responsibility of the SDK provider
 * chain (workload identity, instance role, container role, or local profile).
 */
export function documentStorageConfigFromEnv(
  env: NodeJS.ProcessEnv = process.env,
): S3DocumentStorageConfig | null {
  const bucket = env.DOCUMENT_STORAGE_S3_BUCKET?.trim()
  const region = env.DOCUMENT_STORAGE_S3_REGION?.trim()

  // Storage is an optional capability until upload routes are composed. A
  // partially configured capability is rejected rather than silently disabled.
  if (!bucket && !region) return null
  if (!bucket) throw new Error('DOCUMENT_STORAGE_S3_BUCKET is required when document storage is configured')
  if (!region) throw new Error('DOCUMENT_STORAGE_S3_REGION is required when document storage is configured')

  const endpoint = optionalTrimmed(env.DOCUMENT_STORAGE_S3_ENDPOINT)
  if (endpoint) {
    const parsed = new URL(endpoint)
    if (parsed.protocol !== 'https:') {
      throw new Error('DOCUMENT_STORAGE_S3_ENDPOINT must use HTTPS')
    }
  }

  const uploadTtlSeconds = parseUploadTtl(env.DOCUMENT_STORAGE_UPLOAD_TTL_SECONDS)
  const forcePathStyle = parseBoolean(env.DOCUMENT_STORAGE_S3_FORCE_PATH_STYLE)

  return {
    bucket,
    region,
    ...(endpoint ? { endpoint } : {}),
    ...(forcePathStyle === undefined ? {} : { forcePathStyle }),
    uploadTtlSeconds,
  }
}

function optionalTrimmed(value: string | undefined): string | undefined {
  const trimmed = value?.trim()
  return trimmed ? trimmed : undefined
}

function parseUploadTtl(value: string | undefined): number {
  if (value === undefined || value.trim() === '') return DEFAULT_UPLOAD_TTL_SECONDS
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed <= 0 || parsed > MAX_UPLOAD_TTL_SECONDS) {
    throw new Error('DOCUMENT_STORAGE_UPLOAD_TTL_SECONDS must be an integer between 1 and 900')
  }
  return parsed
}

function parseBoolean(value: string | undefined): boolean | undefined {
  if (value === undefined || value.trim() === '') return undefined
  const normalized = value.trim().toLowerCase()
  if (normalized === 'true') return true
  if (normalized === 'false') return false
  throw new Error('DOCUMENT_STORAGE_S3_FORCE_PATH_STYLE must be true or false')
}
