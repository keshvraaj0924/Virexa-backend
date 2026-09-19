import { Pool } from 'pg'
import type { OrganizationHierarchy } from '../contracts/organization.js'

export interface OrganizationHierarchyQuery {
  limit: number
  status?: 'active' | 'inactive'
}

function iso(value: Date | string): string {
  return value instanceof Date ? value.toISOString() : new Date(value).toISOString()
}

export class PostgresOrganizationRepository {
  constructor(private readonly pool: Pool) {}

  async hierarchy(organizationId: string, query: OrganizationHierarchyQuery): Promise<OrganizationHierarchy> {
    const statusPredicate = query.status ? ' AND status = $3' : ''
    const params: unknown[] = [organizationId, query.limit]
    if (query.status) params.push(query.status)

    const [branches, departments, personas] = await Promise.all([
      this.pool.query(
        `SELECT id, name, code, status, created_at, updated_at
         FROM branches WHERE organization_id = $1${statusPredicate}
         ORDER BY created_at DESC, id DESC LIMIT $2`, params),
      this.pool.query(
        `SELECT id, branch_id, name, code, status, created_at, updated_at
         FROM departments WHERE organization_id = $1${statusPredicate}
         ORDER BY created_at DESC, id DESC LIMIT $2`, params),
      this.pool.query(
        `SELECT id, department_id, name, description, status, created_at, updated_at
         FROM personas WHERE organization_id = $1${statusPredicate}
         ORDER BY created_at DESC, id DESC LIMIT $2`, params),
    ])

    return {
      branches: branches.rows.map((row) => ({ id: row.id, name: row.name, code: row.code, status: row.status, createdAt: iso(row.created_at), updatedAt: iso(row.updated_at) })),
      departments: departments.rows.map((row) => ({ id: row.id, branchId: row.branch_id, name: row.name, code: row.code, status: row.status, createdAt: iso(row.created_at), updatedAt: iso(row.updated_at) })),
      personas: personas.rows.map((row) => ({ id: row.id, departmentId: row.department_id, name: row.name, description: row.description ?? null, status: row.status, createdAt: iso(row.created_at), updatedAt: iso(row.updated_at) })),
    }
  }

  async close(): Promise<void> {
    await this.pool.end()
  }
}
