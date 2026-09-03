import { createHash } from 'node:crypto'
import { Pool } from 'pg'
import type { AuthSession, UserSummary, UserRole } from '../contracts/auth.js'
import { createSessionToken, hashPassword, verifyPassword } from './crypto.js'

export interface AuthRepository {
  register(input: { displayName: string; email: string; password: string; organizationName: string }): Promise<AuthSession & { sessionToken: string }>
  login(email: string, password: string): Promise<(AuthSession & { sessionToken: string }) | null>
  getSession(token: string): Promise<AuthSession | null>
  revokeSession(token: string): Promise<void>
  close?(): Promise<void>
}

function tokenDigest(token: string): string {
  return createHash('sha256').update(token).digest('hex')
}

function toUser(row: any): UserSummary {
  return {
    id: row.user_id,
    email: row.email,
    displayName: row.display_name,
    role: row.role as UserRole,
    organizationId: row.organization_id,
    organizationName: row.organization_name,
  }
}

export class PostgresAuthRepository implements AuthRepository {
  constructor(private readonly pool: Pool) {}

  async register(input: { displayName: string; email: string; password: string; organizationName: string }) {
    const client = await this.pool.connect()
    try {
      await client.query('BEGIN')
      const organization = await client.query<{ id: string; name: string }>('INSERT INTO organizations (name) VALUES ($1) RETURNING id, name', [input.organizationName.trim()])
      const passwordHash = await hashPassword(input.password)
      const user = await client.query(
        `INSERT INTO users (organization_id, email, display_name, password_hash, role)
         VALUES ($1, lower($2), $3, $4, 'admin')
         RETURNING id AS user_id, email, display_name, role, organization_id`,
        [organization.rows[0].id, input.email, input.displayName.trim(), passwordHash],
      )
      const token = createSessionToken()
      const session = await client.query(
        `INSERT INTO sessions (user_id, token_digest, expires_at)
         VALUES ($1, $2, now() + interval '8 hours') RETURNING expires_at`,
        [user.rows[0].user_id, tokenDigest(token)],
      )
      await client.query('COMMIT')
      const summary = toUser({ ...user.rows[0], organization_name: organization.rows[0].name })
      return { user: summary, expiresAt: session.rows[0].expires_at.toISOString(), sessionToken: token }
    } catch (error) {
      await client.query('ROLLBACK')
      throw error
    } finally {
      client.release()
    }
  }

  async login(email: string, password: string) {
    const result = await this.pool.query(
      `SELECT u.id AS user_id, u.email, u.display_name, u.password_hash, u.role,
              o.id AS organization_id, o.name AS organization_name
       FROM users u JOIN organizations o ON o.id = u.organization_id
       WHERE u.email = lower($1) AND u.disabled_at IS NULL`,
      [email],
    )
    const row = result.rows[0]
    if (!row || !(await verifyPassword(password, row.password_hash))) return null
    const token = createSessionToken()
    const session = await this.pool.query(
      `INSERT INTO sessions (user_id, token_digest, expires_at)
       VALUES ($1, $2, now() + interval '8 hours') RETURNING expires_at`,
      [row.user_id, tokenDigest(token)],
    )
    return { user: toUser(row), expiresAt: session.rows[0].expires_at.toISOString(), sessionToken: token }
  }

  async getSession(token: string) {
    const result = await this.pool.query(
      `SELECT u.id AS user_id, u.email, u.display_name, u.role,
              o.id AS organization_id, o.name AS organization_name, s.expires_at
       FROM sessions s JOIN users u ON u.id = s.user_id JOIN organizations o ON o.id = u.organization_id
       WHERE s.token_digest = $1 AND s.revoked_at IS NULL AND s.expires_at > now() AND u.disabled_at IS NULL`,
      [tokenDigest(token)],
    )
    const row = result.rows[0]
    return row ? { user: toUser(row), expiresAt: row.expires_at.toISOString() } : null
  }

  async revokeSession(token: string) {
    await this.pool.query('UPDATE sessions SET revoked_at = now() WHERE token_digest = $1 AND revoked_at IS NULL', [tokenDigest(token)])
  }

  async close() {
    await this.pool.end()
  }
}

export function createAuthRepository(databaseUrl: string): PostgresAuthRepository {
  return new PostgresAuthRepository(new Pool({ connectionString: databaseUrl, max: 10 }))
}
