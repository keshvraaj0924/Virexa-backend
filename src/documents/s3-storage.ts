import {
  DeleteObjectCommand,
  HeadObjectCommand,
  PutObjectCommand,
  S3Client,
} from '@aws-sdk/client-s3'
import { getSignedUrl } from '@aws-sdk/s3-request-presigner'
import type {
  DocumentObjectStorageLifecycle,
  DocumentStoredObject,
  DocumentUploadDescriptor,
  DocumentUploadTarget,
} from './storage.js'

const DEFAULT_UPLOAD_TTL_SECONDS = 10 * 60
const MAX_UPLOAD_TTL_SECONDS = 15 * 60

export interface S3DocumentStorageConfig {
  bucket: string
  region: string
  endpoint?: string
  forcePathStyle?: boolean
  uploadTtlSeconds?: number
}

/**
 * Durable document storage backed by S3 or an explicitly configured
 * S3-compatible service. Credentials are resolved exclusively through the
 * AWS SDK credential provider chain; this adapter never accepts static secrets.
 */
export class S3DocumentObjectStorage implements DocumentObjectStorageLifecycle {
  private readonly client: S3Client
  private readonly uploadTtlSeconds: number

  constructor(private readonly config: S3DocumentStorageConfig, client?: S3Client) {
    const bucket = config.bucket.trim()
    const region = config.region.trim()
    if (!bucket) throw new Error('Document storage bucket is required')
    if (!region) throw new Error('Document storage region is required')

    const uploadTtlSeconds = config.uploadTtlSeconds ?? DEFAULT_UPLOAD_TTL_SECONDS
    if (!Number.isInteger(uploadTtlSeconds) || uploadTtlSeconds <= 0 || uploadTtlSeconds > MAX_UPLOAD_TTL_SECONDS) {
      throw new Error('Document upload TTL must be between 1 and 900 seconds')
    }

    if (config.endpoint) {
      const endpoint = new URL(config.endpoint)
      if (endpoint.protocol !== 'https:') throw new Error('Document storage endpoint must use HTTPS')
    }

    this.uploadTtlSeconds = uploadTtlSeconds
    this.client = client ?? new S3Client({
      region,
      endpoint: config.endpoint,
      forcePathStyle: config.forcePathStyle,
    })
  }

  async createUploadTarget(descriptor: DocumentUploadDescriptor, objectKey: string): Promise<DocumentUploadTarget> {
    const checksum = descriptor.checksumSha256
    const command = new PutObjectCommand({
      Bucket: this.config.bucket,
      Key: objectKey,
      ContentType: descriptor.mediaType,
      ContentLength: descriptor.sizeBytes,
      Metadata: { 'virexa-sha256': checksum },
    })
    const uploadUrl = await getSignedUrl(this.client, command, { expiresIn: this.uploadTtlSeconds })

    return {
      objectKey,
      uploadUrl,
      expiresAt: new Date(Date.now() + this.uploadTtlSeconds * 1000).toISOString(),
      requiredHeaders: {
        'content-type': descriptor.mediaType,
        'content-length': String(descriptor.sizeBytes),
        'x-virexa-sha256': checksum,
        'x-amz-meta-virexa-sha256': checksum,
      },
    }
  }

  async inspectObject(objectKey: string): Promise<DocumentStoredObject | null> {
    try {
      const response = await this.client.send(new HeadObjectCommand({
        Bucket: this.config.bucket,
        Key: objectKey,
      }))
      const checksumSha256 = response.Metadata?.['virexa-sha256']
      if (!response.ContentType || response.ContentLength === undefined || !checksumSha256) return null

      return {
        objectKey,
        mediaType: response.ContentType,
        sizeBytes: response.ContentLength,
        checksumSha256,
      }
    } catch (error) {
      if (isNotFound(error)) return null
      throw error
    }
  }

  async deleteObject(objectKey: string): Promise<void> {
    await this.client.send(new DeleteObjectCommand({
      Bucket: this.config.bucket,
      Key: objectKey,
    }))
  }
}

function isNotFound(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false
  const candidate = error as { name?: string; $metadata?: { httpStatusCode?: number } }
  return candidate.name === 'NotFound' || candidate.name === 'NoSuchKey' || candidate.$metadata?.httpStatusCode === 404
}
