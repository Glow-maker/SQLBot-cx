<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus-secondary'
import { getIndexSyncControl, getIndexJob, syncAllIndexes, saveIndexSchedule, type IndexSyncControl, type IndexSyncItem, type IndexJob } from '@/api/indexSync'
import { indexFailureReason, indexFailureTable } from './indexFailure'

const control = ref<IndexSyncControl>()
const error = ref('')
const loading = ref(false)
const submitting = ref(false)
const saving = ref(false)
const settingsOpen = ref(false)
const force = ref(false)
const form = reactive({ enabled: false, time: '22:00', force: false })
const active = computed(() => ['pending', 'running'].includes(control.value?.batch?.status || ''))
const items = computed(() => control.value?.batch?.items || [])
const finished = computed(() => items.value.filter(item => !['pending', 'running'].includes(item.status)).length)
const totals = computed(() => items.value.reduce((sum, item) => ({ processed: sum.processed + (item.counts?.processed || 0), failed: sum.failed + (item.counts?.failed || 0) }), { processed: 0, failed: 0 }))
const failureDetails = reactive<Record<string, { loading: boolean; error: string; errors: IndexJob['errors'] }>>({})
const statusText: Record<string, string> = { pending: '等待中', running: '同步中', succeeded: '同步完成', partial_failed: '部分失败', failed: '失败' }
const errorText = (code: string) => ({ OWNER_UNAUTHORIZED: '执行账号权限已变化，请重新确认授权', INDEX_REQUEST_REJECTED: '同步被拒绝，请检查数据源和权限', INDEX_SERVICE_UNAVAILABLE: '索引服务暂不可用', BATCH_TIMEOUT: '任务超时，请检查服务后重新同步' }[code] || '同步失败，请查看服务日志')
const timeText = (time: string) => new Date(time).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
const message = (e: unknown) => e instanceof Error ? e.message : '操作失败，请重试'
let timer: ReturnType<typeof setTimeout> | undefined
let polling: AbortController | undefined
const lifetime = new AbortController()
async function loadFailures(row: IndexSyncItem) {
  if (!row.jobId || failureDetails[row.jobId]?.loading) return
  const jobId = row.jobId
  failureDetails[jobId] = { loading: true, error: '', errors: [] }
  const detail = failureDetails[jobId]!
  try {
    const job = await getIndexJob(jobId, lifetime.signal)
    if (lifetime.signal.aborted) return
    if (job.jobId !== jobId || String(job.datasourceId) !== String(row.datasourceId) || !Array.isArray(job.errors)) throw new Error('Invalid task')
    detail.errors = job.errors
  } catch (e) {
    if (!lifetime.signal.aborted) detail.error = (e as { response?: { status?: number } })?.response?.status === 403
      ? '当前账号无权读取任务明细，请联系索引管理员。' : '暂时无法读取失败明细，请重试或联系管理员。'
  } finally { detail.loading = false }
}
function stopPolling() { clearTimeout(timer); polling?.abort() }
async function refresh() {
  stopPolling()
  if (lifetime.signal.aborted) return
  const request = new AbortController()
  polling = request
  loading.value = true
  try {
    const value = await getIndexSyncControl(request.signal)
    if (request.signal.aborted || lifetime.signal.aborted) return
    control.value = value
    error.value = ''
  } catch (e) {
    if (!request.signal.aborted && !lifetime.signal.aborted) { error.value = message(e); control.value = undefined }
  } finally {
    if (!request.signal.aborted && !lifetime.signal.aborted) {
      loading.value = false
      if (!error.value) timer = setTimeout(refresh, active.value ? 2000 : 15000)
    }
  }
}
async function submit() {
  if (!control.value || active.value || submitting.value) return
  if (force.value) {
    try { await ElMessageBox.confirm('强制重建会重新计算全部已登记表的向量，耗时较长。是否继续？', '强制重建索引', { type: 'warning', confirmButtonText: '开始重建', cancelButtonText: '取消' }) }
    catch { return }
  }
  if (lifetime.signal.aborted) return
  submitting.value = true
  stopPolling()
  try {
    control.value = await syncAllIndexes(force.value, lifetime.signal)
    ElMessage.success('索引同步已提交，关闭页面后任务仍会继续')
  } catch (e) { if (!lifetime.signal.aborted) ElMessage.error(message(e)) }
  finally { submitting.value = false; void refresh() }
}
function openSettings() {
  if (!control.value) return
  Object.assign(form, { enabled: control.value.enabled, time: control.value.time, force: control.value.force })
  settingsOpen.value = true
}
async function save() {
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(form.time)) { ElMessage.warning('请选择有效的执行时间'); return }
  saving.value = true
  stopPolling()
  try {
    control.value = await saveIndexSchedule({ ...form }, lifetime.signal)
    settingsOpen.value = false
    ElMessage.success('自动同步设置已保存')
  } catch (e) { if (!lifetime.signal.aborted) ElMessage.error(message(e)) }
  finally { saving.value = false; void refresh() }
}
onMounted(refresh)
onBeforeUnmount(() => { lifetime.abort(); stopPolling() })
</script>

<template>
  <section class="index-page" aria-labelledby="index-title">
    <header class="page-heading">
      <div><h1 id="index-title">问数索引</h1><p>更新表结构索引，让智能问数找到相关数据表。</p></div>
      <el-button :loading="loading" :disabled="submitting || saving" @click="refresh">刷新状态</el-button>
    </header>
    <el-alert v-if="error" :title="error" type="warning" :closable="false" show-icon />
    <div v-if="control" class="status-summary">
      <div><span>最近一次同步开始</span><strong>{{ control.batch ? timeText(control.batch.startedAt) : '尚无记录' }}</strong></div>
      <div><span>本次已处理表数</span><strong>{{ totals.processed }}</strong></div>
      <div :class="{ failure: totals.failed > 0 }"><span>本次失败表数</span><strong>{{ totals.failed }}</strong></div>
    </div>
    <div class="sync-panel">
      <div class="sync-intro"><h2>生成与更新索引</h2><el-tag v-if="control" :type="control.enabled ? 'success' : 'info'">{{ control.enabled ? `每天 ${control.time} 自动同步` : '自动同步未开启' }}</el-tag></div>
      <p>与中台数据源管理共用同步任务和定时设置。默认仅更新变化的表，已生成且未变化的索引会自动跳过。</p>
      <div class="actions">
        <el-button type="primary" :loading="submitting" :disabled="!control || active || saving" @click="submit">{{ active ? '正在同步' : '一键同步全部' }}</el-button>
        <el-checkbox v-model="force" :disabled="!control || active || submitting || saving">强制重建向量</el-checkbox>
        <el-button :disabled="!control || submitting || saving" @click="openSettings">自动同步设置</el-button>
      </div>
      <div class="scope-note">同步范围为当前租户全部已启用数据源中已登记的表结构，包含尚未挂载或上线的表。索引生成不会授予查询权限；问数仍只使用当前用户有权查询的表。未登记的表需先在中台同步物理元数据。</div>
    </div>
    <div class="progress-panel">
      <div class="progress-heading"><h2>最近一次同步</h2><span v-if="control?.batch">{{ statusText[control.batch.status] || control.batch.status }} · {{ finished }}/{{ items.length }} 个数据源</span></div>
      <template v-if="control?.batch">
        <p class="timestamps">开始于 {{ timeText(control.batch.startedAt) }}<span v-if="control.batch.finishedAt"> · 结束于 {{ timeText(control.batch.finishedAt) }}</span>（北京时间）</p>
        <el-alert v-if="control.batch.error" :title="errorText(control.batch.error)" type="error" :closable="false" />
        <el-table :data="items" row-key="datasourceId" class="task-table" empty-text="本次没有已启用的数据源">
          <el-table-column type="expand" width="40"><template #default="{ row }">
            <div class="failure-details">
              <template v-if="row.jobId && (row.counts?.failed > 0 || ['failed', 'partial_failed'].includes(row.status))">
                <el-button :loading="failureDetails[row.jobId]?.loading" @click="loadFailures(row)">{{ failureDetails[row.jobId] ? '刷新失败明细' : '查看失败明细' }}</el-button>
                <p v-if="failureDetails[row.jobId]?.error" class="failure">{{ failureDetails[row.jobId].error }}</p>
                <ul v-else-if="failureDetails[row.jobId]?.errors.length"><li v-for="(failure, i) in failureDetails[row.jobId].errors" :key="i"><strong>{{ indexFailureTable(failure.tableId) }}</strong>：{{ indexFailureReason(failure.code) }}</li></ul>
                <p v-else-if="failureDetails[row.jobId] && !failureDetails[row.jobId].loading">任务未返回失败明细，请管理员根据本次同步记录检查服务日志。</p>
                <p>任务最多保留 20 条失败原因。按表 ID 在中台定位表；修正后可使用上方“一键同步全部”重新执行。</p>
              </template>
              <p v-else>{{ row.error ? errorText(row.error) : '此数据源暂无可读取的失败任务明细。' }}</p>
            </div>
          </template></el-table-column>
          <el-table-column prop="name" label="数据源" min-width="180" />
          <el-table-column label="状态" width="130"><template #default="{ row }"><el-tag :type="row.status === 'succeeded' ? 'success' : ['failed', 'partial_failed'].includes(row.status) ? 'danger' : 'info'">{{ statusText[row.status] || row.status }}</el-tag></template></el-table-column>
          <el-table-column label="表进度" width="110"><template #default="{ row }">{{ row.counts?.processed ?? 0 }} / {{ row.counts?.total ?? 0 }}</template></el-table-column>
          <el-table-column label="同步结果" min-width="320"><template #default="{ row }"><span v-if="row.error" class="failure">{{ errorText(row.error) }}</span><span v-else>新增 {{ row.counts?.added ?? 0 }} · 更新 {{ row.counts?.updated ?? 0 }} · 跳过 {{ row.counts?.skipped ?? 0 }} · 失效 {{ row.counts?.invalidated ?? 0 }} · 失败 {{ row.counts?.failed ?? 0 }}</span></template></el-table-column>
        </el-table>
      </template>
      <el-empty v-else :description="error ? '暂时无法读取同步记录' : loading ? '正在读取同步记录' : '暂无同步记录，点击一键同步开始生成索引'" :image-size="72" />
    </div>
    <el-dialog v-model="settingsOpen" title="自动同步设置" width="min(480px, 92vw)">
      <el-form label-position="top">
        <el-form-item label="自动同步"><el-switch v-model="form.enabled" /></el-form-item>
        <el-form-item label="每天执行时间（北京时间）"><el-time-select v-model="form.time" start="00:00" end="23:59" step="00:01" /></el-form-item>
        <el-form-item><el-checkbox v-model="form.force">每次强制重建全部向量</el-checkbox></el-form-item>
      </el-form>
      <p class="scope-note">由服务端执行，以保存设置账号的权限为准。关闭开关仅停止后续自动任务；服务恢复时会补跑当天尚未执行的计划。</p>
      <template #footer><el-button :disabled="saving" @click="settingsOpen = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存设置</el-button></template>
    </el-dialog>
  </section>
</template>

<style scoped lang="less">
.index-page { padding: 28px; height: 100%; overflow: auto; color: #1f2937; }
.page-heading, .sync-intro, .progress-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
h1 { font-size: 22px; font-weight: 600; margin: 0 0 8px; }
h2 { font-size: 16px; font-weight: 600; margin: 0; }
p { color: #667085; font-size: 14px; line-height: 1.7; margin: 10px 0 18px; }
.sync-panel, .progress-panel { margin-top: 22px; padding: 24px; border: 1px solid #e4e7ec; border-radius: 12px; background: #fff; }
.actions { display: flex; align-items: center; flex-wrap: wrap; gap: 16px; }
.scope-note { margin-top: 18px; padding: 12px 16px; background: #f8fafc; border-radius: 8px; font-size: 13px; line-height: 1.8; color: #667085; }
.progress-heading span, .timestamps { font-size: 13px; color: #667085; }
.failure { color: #d92d20; }
.task-table { margin-top: 14px; }
.status-summary { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 12px; margin-top: 20px; }
.status-summary > div { border: 1px solid #e4e7ec; border-radius: 10px; padding: 16px; background: #fff; }
.status-summary span, .status-summary strong { display: block; }
.status-summary span { font-size: 12px; color: #667085; margin-bottom: 7px; }
.status-summary strong { font-size: 18px; font-variant-numeric: tabular-nums; }
.failure-details { padding: 12px 24px; }
.failure-details ul { padding-left: 20px; line-height: 1.8; }
@media (max-width: 720px) { .status-summary { grid-template-columns: 1fr 1fr; } .status-summary > div:first-child { grid-column: 1 / -1; } }
@media (max-width: 720px) { .index-page { padding: 16px; } .sync-panel, .progress-panel { padding: 16px; } }
</style>
