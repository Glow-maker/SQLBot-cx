import axios from 'axios'
import { request } from '@/utils/request'

export interface IndexSyncItem { datasourceId: number; name: string; status: string; jobId?: string; error?: string; counts: Record<string, number> }
export interface IndexJob { jobId: string; datasourceId: number; errors: { tableId?: number; code: string }[] }

export interface IndexSyncControl {
  enabled: boolean
  time: string
  timezone: string
  force: boolean
  batch?: {
    id: string
    status: string
    startedAt: string
    finishedAt?: string
    error?: string
    items?: IndexSyncItem[]
  }
}

const url = '/admin-api/dmgt/data-source/index-sync'
const identity = () => window.__SQLBOT_GET_LOGIN_STATE__?.() ?? window.__INJECTED_LOGIN_STATE__
let renewal: Promise<unknown> | undefined

// Resolve the current token's tenant instead of trusting the host's cached tenant.
async function call(method: 'GET' | 'POST' | 'PUT', path: string, data?: unknown, signal?: AbortSignal): Promise<IndexSyncControl> {
  let verifiedIdentity: string | undefined
  for (let attempt = 0; attempt < 2; attempt++) {
    const state = identity()
    if (!state?.token) {
      throw new Error('请从业务中台登录后进入索引管理')
    }
    let response = await axios.request({
      method: 'GET', baseURL: import.meta.env.VITE_API_BASE_URL, url: '/user/info', signal, timeout: 20000,
      headers: { 'X-SQLBOT-TOKEN': `Bearer ${state.token}` },
      validateStatus: () => true,
    })
    if (identity()?.token !== state.token) throw new Error('登录状态已变化，请重新进入索引管理')
    if (response.status === 200) {
      const user = response.data
      if (user?.auth_source !== 'platform' || user.id == null || !Number.isSafeInteger(user.oid) || user.oid < 0) {
        throw new Error('无法确认中台登录身份，请重新进入索引管理')
      }
      const currentIdentity = `${user.id}:${user.oid}`
      if (verifiedIdentity && verifiedIdentity !== currentIdentity) throw new Error('登录状态已变化，请重新进入索引管理')
      verifiedIdentity = currentIdentity
      response = await axios.request({
        method, url: url + path, data, signal, timeout: 20000,
        headers: { Authorization: `Bearer ${state.token}`, 'tenant-id': String(user.oid) },
        validateStatus: () => true,
      })
    } else if (response.status !== 401) {
      throw new Error('无法确认中台登录身份，请重新进入索引管理')
    }
    const code = response.data?.code ?? response.status
    if ((response.status === 401 || code === 401) && attempt === 0 && window.__SQLBOT_REFRESH_LOGIN_STATE__) {
      renewal ??= window.__SQLBOT_REFRESH_LOGIN_STATE__().finally(() => { renewal = undefined })
      await renewal
      if (method !== 'GET' && !verifiedIdentity) throw new Error('登录状态已刷新，请重新提交索引操作')
      continue
    }
    if (identity()?.token !== state.token) throw new Error('登录状态已变化，请重新进入索引管理')
    if (response.status === 403 || code === 403) throw new Error('当前账号没有中台索引同步权限，请联系管理员配置')
    if (response.status === 401 || code === 401) throw new Error('登录已失效，请重新登录')
    const value = response.data?.data
    if (response.status !== 200 || code !== 0 || typeof value?.enabled !== 'boolean' || typeof value?.time !== 'string') {
      throw new Error('无法获取中台索引配置，请检查服务后重试')
    }
    return value
  }
  throw new Error('登录已失效，请重新登录')
}

export const getIndexSyncControl = (signal?: AbortSignal) => call('GET', '', undefined, signal)
export const getIndexJob = (jobId: string, signal?: AbortSignal) => request.get<IndexJob>(
  `/platform/table-embeddings/sync/${encodeURIComponent(jobId)}`, { signal, timeout: 20000, requestOptions: { customError: true } })
export const syncAllIndexes = (force: boolean, signal?: AbortSignal) => call('POST', '/all', { force }, signal)
export const saveIndexSchedule = (data: { enabled: boolean; time: string; force: boolean }, signal?: AbortSignal) =>
  call('PUT', '/schedule', data, signal)
