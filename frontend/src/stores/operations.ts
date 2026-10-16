import { defineStore } from 'pinia'
import { store } from './index'
import {
  operationsApi,
  type OperationsAccess,
  type OperationsScope,
  type OperationRow,
} from '@/api/operations'
import { canOpenOperations, compactQuery } from '@/views/system/operations/operationsPolicy'

const OperationsStore = defineStore('operations', {
  state: () => ({
    access: {
      instance_admin: false,
      operator: false,
      tenant_id: null,
      auth_source: '',
    } as OperationsAccess,
    scope: 'platform' as OperationsScope,
    tenantId: '' as string,
    tenants: [] as OperationRow[],
    feedbackOpen: false,
    feedbackRecordId: undefined as string | number | undefined,
    feedbackInteractionId: undefined as string | number | undefined,
    feedbackTab: 'submit',
    draft: null as {
      kind: 'terminology' | 'data-training'
      scope: OperationsScope
      tenantId: string
      values: OperationRow
    } | null,
  }),
  getters: {
    allowed: (state) => canOpenOperations(state.access),
    context: (state) => compactQuery({ scope: state.scope, tenant_id: state.tenantId }),
  },
  actions: {
    async loadAccess() {
      try {
        const value = await operationsApi.access()
        this.access = value
        if (!value.instance_admin) {
          this.scope = 'platform'
          this.tenantId = value.tenant_id == null ? '' : String(value.tenant_id)
          this.tenants = []
        } else if (!this.tenantId) {
          this.scope = value.auth_source === 'local' ? 'local' : 'platform'
          this.tenantId = value.tenant_id == null ? '' : String(value.tenant_id)
        }
      } catch {
        this.access = { instance_admin: false, operator: false, tenant_id: null, auth_source: '' }
        this.tenants = []
      }
    },
    async loadTenants() {
      if (!this.access.instance_admin) return
      const result: any = await operationsApi.tenants()
      this.tenants = Array.isArray(result) ? result : result.items || []
    },
    openFeedback(recordId?: string | number, tab = 'submit', interactionId?: string | number) {
      this.feedbackRecordId = recordId
      this.feedbackInteractionId = interactionId
      this.feedbackTab = tab
      this.feedbackOpen = true
    },
  },
})
export const useOperationsStore = () => OperationsStore(store)
