import type { OperationsAccess, OperationQuery, QueryResultOutcome } from '@/api/operations'

export function canOpenOperations(access: Partial<OperationsAccess>) {
  return access.instance_admin === true || access.operator === true
}

export function canOpenSystemRoute(
  meta: Record<string, any>,
  isAdmin: boolean,
  access: Partial<OperationsAccess>
) {
  if (meta.instanceAdmin) return access.instance_admin === true
  if (meta.operations) return canOpenOperations(access)
  return isAdmin
}

export function compactQuery(values: Record<string, any>): OperationQuery {
  return Object.fromEntries(
    Object.entries(values).filter(([, value]) => value !== '' && value != null)
  )
}

export const feedbackTypes = [
  { value: 'incorrect', label: '结果不准确' },
  { value: 'error', label: '执行报错' },
  { value: 'slow', label: '响应慢' },
  { value: 'permission', label: '权限问题' },
  { value: 'suggestion', label: '改进建议' },
  { value: 'other', label: '其他' },
]
export const feedbackStatuses = [
  { value: 'pending', label: '待处理' },
  { value: 'processing', label: '处理中' },
  { value: 'resolved', label: '已处理' },
  { value: 'declined', label: '不予处理' },
]
export function statusLabel(status: string) {
  return (
    feedbackStatuses.find((item) => item.value === status)?.label ||
    (
      {
        success: '执行成功',
        error: '执行错误',
        failed: '失败',
        timeout: '超时',
        permission_denied: '无权限',
        index_not_ready: '索引未就绪',
        no_match: '未找到匹配表',
        clarification: '待澄清',
        interrupted: '已中断',
        running: '执行中',
        queued: '排队中',
        completed: '已完成',
        cancelled: '已取消',
      } as Record<string, string>
    )[status] ||
    status ||
    '—'
  )
}
export const interactionStatuses = [
  'running',
  'success',
  'error',
  'timeout',
  'permission_denied',
  'index_not_ready',
  'no_match',
  'clarification',
  'interrupted',
]
export const queryResultOptions: { value: QueryResultOutcome; label: string }[] = [
  { value: 'empty_result', label: '返回 0 行' },
  { value: 'all_null_result', label: '返回行全为空值' },
  { value: 'rows_returned', label: '有结果' },
]
export function queryResultLabel(outcome: unknown) {
  return queryResultOptions.find((item) => item.value === outcome)?.label || '未记录'
}
export function dateQuery(range: string[] | null | undefined) {
  if (!range?.[0] || !range?.[1]) return {}
  const start = new Date(`${range[0]}T00:00:00`)
  const end = new Date(`${range[1]}T00:00:00`)
  end.setDate(end.getDate() + 1)
  return { date_from: start.toISOString(), date_to: end.toISOString() }
}
export function feedbackTypeLabel(type: string) {
  return feedbackTypes.find((item) => item.value === type)?.label || type || '—'
}
export function displayDuration(milliseconds: unknown) {
  if (milliseconds == null || milliseconds === '') return '—'
  const value = Number(milliseconds)
  return Number.isFinite(value) ? `${(value / 1000).toFixed(2)} 秒` : '—'
}
export function makeSubmissionId() {
  if (globalThis.crypto.randomUUID) return globalThis.crypto.randomUUID()
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6]! & 15) | 64
  bytes[8] = (bytes[8]! & 63) | 128
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function displayTime(value: unknown) {
  if (typeof value !== 'string' || !value) return '—'
  const iso = value.includes('T') ? value : value.replace(' ', 'T')
  const date = new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}
