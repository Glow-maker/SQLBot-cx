import { request } from '@/utils/request'

export type OperationsScope = 'local' | 'platform'
export type QueryResultOutcome = 'empty_result' | 'all_null_result' | 'rows_returned'
export interface OperationsAccess {
  instance_admin: boolean
  operator: boolean
  tenant_id?: string | number | null
  auth_source: string
}
export type OperationRow = Record<string, any>
export interface OperationsPage {
  items: OperationRow[]
  total: number
  page: number
  size: number
}
export type OperationQuery = Record<string, string | number | undefined> & {
  outcome?: QueryResultOutcome
}
export const operationsApi = {
  access: () =>
    request.get<OperationsAccess>('/operations/access', { requestOptions: { silent: true } }),
  tenants: () => request.get<OperationRow[]>('/operations/tenants'),
  overview: (params: OperationQuery) =>
    request.get<OperationRow>('/operations/overview', { params }),
  page: (kind: string, params: OperationQuery) =>
    request.get<OperationsPage>(`/operations/${kind}`, { params }),
  interaction: (id: string | number, params: OperationQuery) =>
    request.get<OperationRow>(`/operations/interactions/${id}`, { params }),
  submitFeedback: (data: OperationRow) => request.post('/operations/feedback', data),
  myFeedback: (params: OperationQuery) =>
    request.get<OperationsPage>('/operations/feedback/mine', { params }),
  resolveFeedback: (id: string | number, data: OperationRow, params: OperationQuery) =>
    request.request({ method: 'PATCH', url: `/operations/feedback/${id}`, data, params }),
  grant: (data: OperationRow) => request.put('/operations/grants', data),
  report: (id: string | number, params: OperationQuery) =>
    request.get<OperationRow>(`/operations/reports/${id}`, { params }),
  createReport: (data: OperationRow) => request.post('/operations/reports', data),
  retryReport: (id: string | number, params: OperationQuery) =>
    request.post(`/operations/reports/${id}/retry`, {}, { params }),
  catalog: (params: OperationQuery) =>
    request.get<OperationRow>('/operations/knowledge/catalog', { params }),
  refreshCatalog: (params: OperationQuery) =>
    request.post('/operations/knowledge/catalog/refresh', {}, { params }),
}
