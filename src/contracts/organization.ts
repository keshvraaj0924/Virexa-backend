export type OrganizationResourceStatus = 'active' | 'inactive'

export interface OrganizationBranch {
  id: string
  name: string
  code: string
  status: OrganizationResourceStatus
  createdAt: string
  updatedAt: string
}

export interface OrganizationDepartment {
  id: string
  branchId: string
  name: string
  code: string
  status: OrganizationResourceStatus
  createdAt: string
  updatedAt: string
}

export interface OrganizationPersona {
  id: string
  departmentId: string
  name: string
  description: string | null
  status: OrganizationResourceStatus
  createdAt: string
  updatedAt: string
}

export interface OrganizationHierarchy {
  branches: OrganizationBranch[]
  departments: OrganizationDepartment[]
  personas: OrganizationPersona[]
}
