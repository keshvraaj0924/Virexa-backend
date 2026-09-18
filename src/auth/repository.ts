import { createHash } from 'node:crypto'
import { Pool, type PoolClient } from 'pg'
import type { AuthSession, SessionSummary, UserSummary, UserRole } from '../contracts/auth.js'
import { createSessionToken, hashPassword, verifyPassword } from './crypto.js'

const SESSION_LIFETIME_HOURS = 8
const MAX_ACTIVE_SESSIONS_PER_USER = 5

export interface AuthRepository {
  register(input: { displayName: string; email: string; password: string; organizationName: string }): Promise<AuthSession & { sessionToken: string }>
  login(email: string, password: string): Promise<(AuthSession & { sessionToken: string }) | null>
  getSession(token: string): Promise<AuthSession | null>
  listActiveSessions(token: string): Promise<SessionSummary[]>
  revokeSession(token: string): Promise<void>
  revokeOwnedSession(token: string, sessionId: string): Promise<{ revoked: boolean; current: boolean }>
  revokeOtherSessions(token: string): Promise<number>
  ping(): Promise<void>
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

async function createBoundedSession(client: PoolClient, userId: string): Promise<{ sessionToken: string; expiresAt: string }> {
  const token = createSessionToken()
  const session = await client.query(
    `INSERT INTO sessions (user_id, token_digest, expires_at)
     VALUES ($1, $2, now() + ($3 * interval '1 hour')) RETURNING expires_at`,
    [userId, tokenDigest(token), SESSION_LIFETIME_HOURS],
  )

  await client.query(
    `UPDATE sessions
     SET revoked_at = now()
     WHERE user_id = $1
       AND revoked_at IS NULL
       AND expires_at > now()
       AND id NOT IN (
         SELECT id FROM sessions
         WHERE user_id = $1 AND revoked_at IS NULL AND expires_at > now()
         ORDER BY created_at DESC, id DESC
         LIMIT $2
       )`,
    [userId, MAX_ACTIVE_SESSIONS_PER_USER],
  )

  return { sessionToken: token, expiresAt: session.rows[0].expires_at.toISOString() }
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
      const session = await createBoundedSession(client, user.rows[0].user_id)
      await client.query('COMMIT')
      const summary = toUser({ ...user.rows[0], organization_name: organization.rows[0].name })
      return { user: summary, expiresAt: session.expiresAt, sessionToken: session.sessionToken }
    } catch (error) {
      await client.query('ROLLBACK')
      throw error
    } finally {
      client.release()
    }
  }

  async login(email: string, password: string) {
    const candidate = await this.pool.query(
      `SELECT u.id AS user_id, u.password_hash
       FROM users u
       WHERE u.email = lower($1) AND u.disabled_at IS NULL`,
      [email],
    )
    const candidateRow = candidate.rows[0]
    if (!candidateRow || !(await verifyPassword(password, candidateRow.password_hash))) return null

    const client = await this.pool.connect()
    try {
      await client.query('BEGIN')
      const result = await client.query(
        `SELECT u.id AS user_id, u.email, u.display_name, u.password_hash, u.role,
                o.id AS organization_id, o.name AS organization_name
         FROM users u JOIN organizations o ON o.id = u.organization_id
         WHERE u.id = $1 AND u.disabled_at IS NULL AND u.password_hash = $2
         FOR UPDATE OF u`,
        [candidateRow.user_id, candidateRow.password_hash],
      )
      const row = result.rows[0]
      if (!row) {
        await client.query('ROLLBACK')
        return null
      }
      const session = await createBoundedSession(client, row.user_id)
      await client.query('COMMIT')
      return { user: toUser(row), expiresAt: session.expiresAt, sessionToken: session.sessionToken }
    } catch (error) {
      await client.query('ROLLBACK')
      throw error
    } finally {
      client.release()
    }
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

  async listActiveSessions(token: string) {
    const digest = tokenDigest(token)
    const result = await this.pool.query(
      `SELECT s.id, s.created_at, s.expires_at, s.token_digest = $1 AS current
       FROM sessions s
       WHERE s.user_id = (
         SELECT current_session.user_id
         FROM sessions current_session
         WHERE current_session.token_digest = $1
           AND current_session.revoked_at IS NULL
           AND current_session.expires_at > now()
       )
         AND s.revoked_at IS NULL
         AND s.expires_at > now()
       ORDER BY current DESC, s.created_at DESC, s.id DESC`,
      [digest],
    )
    return result.rows.map((row) => ({
      id: row.id,
      createdAt: row.created_at.toISOString(),
      expiresAt: row.expires_at.toISOString(),
      current: row.current,
    }))
  }

  async revokeSession(token: string) {
    await this.pool.query('UPDATE sessions SET revoked_at = now() WHERE token_digest = $1 AND revoked_at IS NULL', [tokenDigest(token)])
  }

  async revokeOwnedSession(token: string, sessionId: string) {
    const digest = tokenDigest(token)
    const result = await this.pool.query<{ current: boolean }>(
      `UPDATE sessions target
       SET revoked_at = now()
       WHERE target.id = $2
         AND target.user_id = (
           SELECT current_session.user_id
           FROM sessions current_session
           WHERE current_session.token_digest = $1
             AND current_session.revoked_at IS NULL
             AND current_session.expires_at > now()
         )
         AND target.revoked_at IS NULL
         AND target.expires_at > now()
       RETURNING target.token_digest = $1 AS current`,
      [digest, sessionId],
    )
    const row = result.rows[0]
    return { revoked: Boolean(row), current: row?.current ?? false }
  }

  async revokeOtherSessions(token: string) {
    const digest = tokenDigest(token)
    const result = await this.pool.query(
      `UPDATE sessions
       SET revoked_at = now()
       WHERE user_id = (
         SELECT user_id FROM sessions
         WHERE token_digest = $1 AND revoked_at IS NULL AND expires_at > now()
       )
         AND token_digest <> $1
         AND revoked_at IS NULL
         AND expires_at > now()`,
      [digest],
    )
    return result.rowCount ?? 0
  }

  async ping() {
    await this.pool.query('SELECT 1')
  }

  async close() {
    await this.pool.end()
  }
}

export function createAuthRepository(databaseUrl: string): PostgresAuthRepository {
  return new PostgresAuthRepository(new Pool({ connectionString: databaseUrl, max: 10 }))
}
