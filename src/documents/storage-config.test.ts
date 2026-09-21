import { describe, expect, it } from 'vitest'
import { documentStorageConfigFromEnv } from './storage-config.js'

describe('documentStorageConfigFromEnv', () => {
  it('keeps binary upload capability disabled when storage is not configured', () => {
    expect(documentStorageConfigFromEnv({})).toBeNull()
  })

  it('builds production storage config without accepting credentials', () => {
    const config = documentStorageConfigFromEnv({
      DOCUMENT_STORAGE_S3_BUCKET: ' virexa-documents ',
      DOCUMENT_STORAGE_S3_REGION: ' ap-south-1 ',
      DOCUMENT_STORAGE_S3_ENDPOINT: 'https://objects.example.com',
      DOCUMENT_STORAGE_S3_FORCE_PATH_STYLE: 'true',
      DOCUMENT_STORAGE_UPLOAD_TTL_SECONDS: '300',
      AWS_ACCESS_KEY_ID: 'must-not-be-copied',
      AWS_SECRET_ACCESS_KEY: 'must-not-be-copied',
    })

    expect(config).toEqual({
      bucket: 'virexa-documents',
      region: 'ap-south-1',
      endpoint: 'https://objects.example.com',
      forcePathStyle: true,
      uploadTtlSeconds: 300,
    })
    expect(config).not.toHaveProperty('credentials')
  })

  it('rejects partial configuration', () => {
    expect(() => documentStorageConfigFromEnv({
      DOCUMENT_STORAGE_S3_BUCKET: 'virexa-documents',
    })).toThrow(/REGION/)

    expect(() => documentStorageConfigFromEnv({
      DOCUMENT_STORAGE_S3_REGION: 'ap-south-1',
    })).toThrow(/BUCKET/)
  })

  it('rejects insecure endpoints and invalid TTLs', () => {
    expect(() => documentStorageConfigFromEnv({
      DOCUMENT_STORAGE_S3_BUCKET: 'virexa-documents',
      DOCUMENT_STORAGE_S3_REGION: 'ap-south-1',
      DOCUMENT_STORAGE_S3_ENDPOINT: 'http://objects.example.com',
    })).toThrow(/HTTPS/)

    expect(() => documentStorageConfigFromEnv({
      DOCUMENT_STORAGE_S3_BUCKET: 'virexa-documents',
      DOCUMENT_STORAGE_S3_REGION: 'ap-south-1',
      DOCUMENT_STORAGE_UPLOAD_TTL_SECONDS: '901',
    })).toThrow(/between 1 and 900/)
  })

  it('rejects ambiguous force-path-style values', () => {
    expect(() => documentStorageConfigFromEnv({
      DOCUMENT_STORAGE_S3_BUCKET: 'virexa-documents',
      DOCUMENT_STORAGE_S3_REGION: 'ap-south-1',
      DOCUMENT_STORAGE_S3_FORCE_PATH_STYLE: 'yes',
    })).toThrow(/true or false/)
  })
})
