import { request } from '@/utils/request'
import { useOperationsStore } from '@/stores/operations'
import { operationsApi } from './operations'

export function knowledgeScopeReady() {
  const ops = useOperationsStore()
  return !!ops.tenantId
}
export async function knowledgeSources() {
  if (!knowledgeScopeReady()) return []
  const result = await operationsApi.catalog(useOperationsStore().context)
  return result.sources || []
}
export function knowledgeApi(kind: 'terminology' | 'data-training') {
  const base = `/operations/knowledge/${kind}`
  const context = () => {
    if (!knowledgeScopeReady()) throw new Error('请选择租户或工作空间')
    return { ...useOperationsStore().context }
  }
  return {
    getList: async (page: number, size: number, filter: any) => {
      let params = filter
      if (typeof filter === 'string') {
        const search = new URLSearchParams(filter.replace(/^\?/, ''))
        params = {} as Record<string, string | string[]>
        search.forEach((value, key) => {
          const values = search.getAll(key)
          params[key] = values.length > 1 ? values : value
        })
      }
      return request.get(`${base}/page/${page}/${size}`, {
        params: { ...params, ...context() },
        paramsSerializer: { indexes: null },
      })
    },
    updateEmbedded: async (data: any) => request.put(base, data, { params: context() }),
    deleteEmbedded: async (ids: (number | string)[]) =>
      request.delete(base, { data: ids, params: context() }),
    enable: async (id: number | string, enabled: any) =>
      request.put(
        `${base}/${id}/enabled`,
        { enabled: enabled === true || enabled === 'true' },
        { params: context() }
      ),
  }
}
