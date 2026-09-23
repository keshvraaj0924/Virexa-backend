import { describe, expect, it } from 'vitest'
import {
  createAgentSchema,
  listAgentsQuerySchema,
  updateAgentSchema,
} from './contracts.js'

const personaId = '4c91d4c7-bcc8-49cc-8c4f-355b49e3bb54'

describe('AI agent v1 contracts', () => {
  it('accepts bounded agent creation without client-controlled tenant or runtime fields', () => {
    const parsed = createAgentSchema.parse({
      name: 'Invoice Review Agent',
      description: 'Reviews extracted invoice fields before ERP handoff.',
      personaId,
      instructions: 'Validate invoice totals and flag mismatches for human review.',
    })

    expect(parsed.name).toBe('Invoice Review Agent')
    expect(parsed.personaId).toBe(personaId)
  })

  it.each(['organizationId', 'tenantId', 'createdByUserId', 'version', 'model', 'provider']) (
    'rejects client-controlled %s on creation',
    (field) => {
      expect(() => createAgentSchema.parse({
        name: 'Invoice Review Agent',
        instructions: 'Review invoices.',
        [field]: 'client-controlled',
      })).toThrow()
    },
  )

  it('requires optimistic concurrency and at least one mutation field', () => {
    expect(() => updateAgentSchema.parse({ expectedVersion: 3 })).toThrow()
    expect(updateAgentSchema.parse({ status: 'paused', expectedVersion: 3 })).toEqual({
      status: 'paused',
      expectedVersion: 3,
    })
  })

  it('rejects attempts to mutate tenant and creator identity', () => {
    expect(() => updateAgentSchema.parse({
      name: 'Changed',
      expectedVersion: 1,
      organizationId: 'tenant-a',
    })).toThrow()
    expect(() => updateAgentSchema.parse({
      name: 'Changed',
      expectedVersion: 1,
      createdByUserId: personaId,
    })).toThrow()
  })

  it('provides bounded list defaults and rejects unsupported tenant selectors', () => {
    expect(listAgentsQuerySchema.parse({})).toEqual({ limit: 24 })
    expect(listAgentsQuerySchema.parse({ status: 'active', limit: '50', cursor: 'opaque' })).toEqual({
      status: 'active',
      limit: 50,
      cursor: 'opaque',
    })
    expect(() => listAgentsQuerySchema.parse({ organizationId: 'tenant-a' })).toThrow()
    expect(() => listAgentsQuerySchema.parse({ limit: 101 })).toThrow()
  })
})
