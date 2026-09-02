import { randomBytes, scrypt as nodeScrypt, timingSafeEqual } from 'node:crypto'
import { promisify } from 'node:util'

const scrypt = promisify(nodeScrypt)
const KEY_LENGTH = 64
const SALT_LENGTH = 32
const HASH_VERSION = 'scrypt-v1'

export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(SALT_LENGTH)
  const derivedKey = (await scrypt(password, salt, KEY_LENGTH)) as Buffer
  return `${HASH_VERSION}$${salt.toString('base64url')}$${derivedKey.toString('base64url')}`
}

export async function verifyPassword(password: string, encodedHash: string): Promise<boolean> {
  const [version, saltValue, hashValue] = encodedHash.split('$')
  if (version !== HASH_VERSION || !saltValue || !hashValue) return false

  try {
    const salt = Buffer.from(saltValue, 'base64url')
    const expected = Buffer.from(hashValue, 'base64url')
    if (salt.length !== SALT_LENGTH || expected.length !== KEY_LENGTH) return false
    const actual = (await scrypt(password, salt, KEY_LENGTH)) as Buffer
    return timingSafeEqual(actual, expected)
  } catch {
    return false
  }
}

export function createSessionToken(): string {
  return randomBytes(48).toString('base64url')
}
