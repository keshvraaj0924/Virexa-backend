import type { Pool, PoolClient } from 'pg'

export interface AuditEventInput {
  organizationId: string
  actorUserId: string | null
  action: string
  resourceType: string
  resourceId?: string | null
  requestId: string
  metadata?: Record<string, unknown>
}

export interface AuditEvent {
  id: string
  organizationId: string
  actorUserId: string | null
  action: string
  resourceType: string
  resourceId: string | null
  requestId: string
  metadata: Record<string, unknown>
  createdAt: string
}

export class AuditService {
  constructor(private readonly pool: Pool) {}

  async record(input: AuditEventInput, client?: PoolClient): Promise<void> {
    const executor = client ?? this.pool
    await executor.query(
      `INSERT INTO audit_events
        (organization_id, actor_user_id, action, resource_type, resource_id, request_id, metadata)
       VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)`,
      [
        input.organizationId,
        input.actorUserId,
        input.action,
        input.resourceType,
        input.resourceId ?? null,
        input.requestId,
        JSON.stringify(input.metadata ?? {}),
      ],
    )
  }

  async listForOrganization(organizationId: string, limit = 50): Promise<AuditEvent[]> {
    const safeLimit = Math.min(Math.max(Math.trunc(limit), 1), 100)
    const result = await this.pool.query(
      `SELECT id, organization_id, actor_user_id, action, resource_type, resource_id,
              request_id, metadata, created_at
       FROM audit_events
       WHERE organization_id = $1
       ORDER BY created_at DESC
       LIMIT $2`,
      [organizationId, safeLimit],
    )
    return result.rows.map((row) => ({
      id: row.id,
      organizationId: row.organization_id,
      actorUserId: row.actor_user_id,
      action: row.action,
      resourceType: row.resource_type,
      resourceId: row.resource_id,
      requestId: row.request_id,
      metadata: row.metadata,
      createdAt: row.created_at.toISOString(),
    }))
  }

  async close() {
    await this.pool.end()
  }
}
