export const stageLabels = {
  route: '查找相关数据表', connect: '连接数据源', generate: '生成查询语句',
  permission: '校验查询权限', query: '查询数据', result: '整理结果与图表',
} as const
export type Stage = keyof typeof stageLabels
export const routeDetailLabels = { metadata: '加载可查询目录', recall: '检索候选表', selection: '模型选择数据表' } as const
type DetailStage = keyof typeof routeDetailLabels
type StageStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
export interface RouteDetail {
  stage: DetailStage
  status: StageStatus
  elapsedMs: number
  updatedAt: number
}
export interface ProgressStep {
  stage: Stage
  status: StageStatus
  elapsedMs: number
  updatedAt: number
  detail: string
  details: RouteDetail[]
}
export interface ChatProgress {
  startedAt: number
  endedAt?: number
  lastEventAt: number
  outcome: 'running' | 'succeeded' | 'failed' | 'stopped'
  structured: boolean
  expanded: boolean
  message: string
  steps: ProgressStep[]
}
export function createProgress(now = Date.now()): ChatProgress {
  return {
    startedAt: now, lastEventAt: now, outcome: 'running', structured: false,
    expanded: false, message: '正在连接问数服务',
    steps: (Object.keys(stageLabels) as Stage[]).map(stage => ({
      stage, status: 'pending', elapsedMs: 0, updatedAt: now, detail: '', details: [],
    })),
  }
}
export function receiveProgress(p: ChatProgress, event: any, now = Date.now()) {
  if (p.outcome !== 'running') return
  p.lastEventAt = now
  if (event.type !== 'progress') return
  const step = p.steps.find(s => s.stage === event.stage)
  if (!step || !['running', 'completed', 'failed', 'skipped'].includes(event.status)) {
    if (!p.structured && typeof event.message === 'string') p.message = event.message
    return
  }
  p.structured = true
  if (['completed', 'failed', 'skipped'].includes(step.status)) return
  step.status = event.status
  step.elapsedMs = Math.max(step.elapsedMs, Number(event.elapsedMs) || 0)
  step.updatedAt = now
  step.detail = typeof event.detail === 'string' ? event.detail : ''
  if (step.status === 'running') p.message = '正在' + stageLabels[step.stage]
  if (step.stage === 'route' && Object.prototype.hasOwnProperty.call(routeDetailLabels, event.detailStage) &&
      ['running', 'completed', 'failed', 'skipped'].includes(event.detailStatus)) {
    let child = step.details.find(d => d.stage === event.detailStage)
    if (!child) {
      child = { stage: event.detailStage, status: 'pending', elapsedMs: 0, updatedAt: now }
      step.details.push(child)
    }
    if (!['completed', 'failed', 'skipped'].includes(child.status)) {
      child.status = event.detailStatus
      child.elapsedMs = Math.max(child.elapsedMs, Number(event.detailElapsedMs) || 0)
      child.updatedAt = now
    }
    const active = step.details.find(d => d.status === 'running')
    if (active && step.status === 'running') p.message = '正在' + routeDetailLabels[active.stage]
  }
}
export function stepElapsed(step: Pick<ProgressStep, 'status' | 'elapsedMs' | 'updatedAt'>, now: number) {
  return step.elapsedMs + (step.status === 'running' ? Math.max(0, now - step.updatedAt) : 0)
}
export function progressWarning(p: ChatProgress, now: number) {
  if (p.outcome !== 'running') return ''
  if (now - p.lastEventAt >= 30000) return '暂未收到新的进度'
  const active = p.steps.find(s => s.status === 'running')
  return active && stepElapsed(active, now) >= 15000 ? '当前步骤耗时较长' : ''
}
export function progressError(error: unknown): string {
  let e: any = error
  if (typeof e === 'string') {
    try { e = JSON.parse(e) } catch { return '处理失败，请查看下方错误信息' }
  }
  if (e?.errorCode === 'authentication_expired' || e?.code === 401) return '登录状态已失效，请重新登录后重试'
  if (e?.type === 'cross_source_not_supported' || e?.reason === 'cross_source_not_supported') return '暂不支持跨数据源查询，请按数据源拆分问题'
  if (e?.type === 'query_timeout') return '问数处理超时，请缩小问题范围或稍后重试'
  if (e?.reason === 'no_query_permission') return '当前没有可查询的数据表，请联系数据管理员授权'
  if (e?.reason === 'index_not_ready') return '问数索引尚未就绪，请联系索引管理员同步后重试'
  if (e?.reason === 'ambiguous_datasource') return '存在同名数据表，请在问题中补充数据源名称'
  if (e?.reason === 'no_relevant_table') return '未找到相关数据表，请补充业务对象或指标，或查看首页可查询目录'
  if (e?.type === 'permission_denied') {
    if (e.errorCode === 'permission_service_error' || e.reason?.startsWith('permission_service_')) return '权限服务暂不可用，查询已停止，请稍后重试'
    return '没有相关数据表的查询权限，请联系数据管理员授权'
  }
  if (e?.type === 'db-connection-err') return '数据源连接失败，请联系管理员检查连接后重试'
  return '处理失败，请查看下方错误信息'
}
export function endProgress(p: ChatProgress, outcome: ChatProgress['outcome'], message: string, now = Date.now(), failedStage?: Stage) {
  if (p.outcome !== 'running') return
  for (const step of p.steps) {
    for (const child of step.details) {
      if (child.status !== 'running') continue
      child.elapsedMs = stepElapsed(child, now)
      child.updatedAt = now
      child.status = outcome === 'failed' ? 'failed' : 'pending'
    }
    if (step.status === 'running' || step.stage === failedStage) {
      step.elapsedMs = stepElapsed(step, now)
      step.updatedAt = now
      // Stopping reception does not prove that the server stopped or failed.
      step.status = outcome === 'failed' ? 'failed' : 'pending'
    }
  }
  p.outcome = outcome
  p.message = message
  p.endedAt = now
  p.expanded = outcome !== 'succeeded'
}
export function completeProgress(p: ChatProgress, empty: boolean, now = Date.now()) {
  if (p.structured && p.steps.some(s => !['completed', 'skipped'].includes(s.status))) {
    endProgress(p, 'failed', '服务端处理结束，但未返回完整阶段状态', now)
    return
  }
  endProgress(p, 'succeeded', empty ? '查询完成，未找到符合条件的数据' : '处理完成', now)
}
