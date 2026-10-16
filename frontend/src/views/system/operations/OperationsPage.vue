<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { operationsApi, type OperationRow } from '@/api/operations'
import { useOperationsStore } from '@/stores/operations'
import { stageLabels, routeDetailLabels } from '@/views/chat/answer/chatProgress'
import OperationsContext from './OperationsContext.vue'
import {
  compactQuery,
  dateQuery,
  displayDuration,
  feedbackTypeLabel,
  feedbackStatuses,
  interactionStatuses,
  queryResultOptions,
  queryResultLabel,
  statusLabel,
  displayTime,
} from './operationsPolicy'

const props = defineProps<{ section: string }>()
const ops = useOperationsStore()
const route = useRoute()
const router = useRouter()
const titles: Record<string, string> = {
  overview: '概览',
  users: '用户分析',
  interactions: '问数明细',
  feedback: '反馈处理',
  reports: '归因报告',
  grants: '授权管理',
}
const title = computed(() => titles[props.section] || '问数运营')
const loading = ref(false)
const error = ref('')
const rows = ref<OperationRow[]>([])
const summary = ref<OperationRow>({})
const total = ref(0)
const page = ref(1)
const size = ref(20)
const dateRange = ref<string[]>([])
const filters = reactive({
  keyword: '',
  user_id: '',
  auth_source: '',
  status: '',
  outcome: '',
  datasource_id: '',
})
const detail = ref<OperationRow | null>(null)
const detailOpen = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
const actionLoading = ref(false)
const reply = reactive({ status: 'processing', reply: '' })
const grantOpen = ref(false)
const grant = reactive({ tenant_id: '', user_id: '', account: '', enabled: true })
let requestVersion = 0
let detailVersion = 0

const query = computed(() =>
  compactQuery({
    ...ops.context,
    ...filters,
    ...dateQuery(dateRange.value),
    page: page.value,
    size: size.value,
    page_size: props.section === 'reports' ? size.value : undefined,
  })
)
const overviewCards = computed(() => [
  { label: '用户提问', value: summary.value.questions ?? '—' },
  { label: '使用人数', value: summary.value.users ?? '—' },
  { label: '重新生成', value: summary.value.retries ?? '—' },
  { label: '执行错误', value: summary.value.errors ?? '—' },
  { label: '返回 0 行', value: summary.value.empty_results ?? '—' },
  { label: '返回行全为空值', value: summary.value.all_null_results ?? '—' },
  { label: '平均耗时（全部执行）', value: displayDuration(summary.value.avg_duration_ms) },
  { label: '待处理反馈', value: summary.value.feedback_pending ?? '—' },
])
const reportFindings = computed<OperationRow[]>(() => {
  const value = detail.value?.result_json?.findings || []
  return Array.isArray(value) ? value : []
})
const reportDrafts = computed<OperationRow[]>(() => detail.value?.result_json?.drafts || [])
function text(value: any) {
  if (value == null || value === '') return '—'
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
}
function rowDuration(row: OperationRow) {
  return displayDuration(
    row.duration_ms ??
      row.total_duration_ms ??
      (row.detailStage ? row.detailElapsedMs : row.elapsedMs) ??
      (row.duration == null ? undefined : Number(row.duration) * 1000)
  )
}
function stageLabel(row: OperationRow) {
  const stage = row.operate || row.stage || row.name
  const label = stageLabels[stage as keyof typeof stageLabels] || stage || '—'
  const detail = row.detailStage
  return detail
    ? `${label} / ${routeDetailLabels[detail as keyof typeof routeDetailLabels] || detail}`
    : label
}
async function load(reset = false) {
  if (reset) page.value = 1
  const version = ++requestVersion
  if (!ops.tenantId && props.section !== 'grants') {
    rows.value = []
    summary.value = {}
    total.value = 0
    loading.value = false
    error.value = '请选择一个租户或工作空间'
    return
  }
  loading.value = true
  error.value = ''
  try {
    if (props.section === 'overview') {
      const data = await operationsApi.overview(query.value)
      if (version === requestVersion) summary.value = data
    } else {
      const data: any = await operationsApi.page(props.section, query.value)
      if (version === requestVersion) {
        rows.value = Array.isArray(data) ? data : data.items || []
        total.value = data.total ?? rows.value.length
      }
    }
  } catch (cause: any) {
    if (version === requestVersion) {
      rows.value = []
      summary.value = {}
      total.value = 0
      error.value = cause?.message || '加载失败，请重试'
    }
  } finally {
    if (version === requestVersion) loading.value = false
  }
}
function reset() {
  Object.assign(filters, {
    keyword: '',
    user_id: '',
    auth_source: '',
    status: '',
    outcome: '',
    datasource_id: '',
  })
  dateRange.value = []
  load(true)
}
async function showDetail(row: OperationRow) {
  const version = ++detailVersion
  detailOpen.value = true
  detailLoading.value = true
  detailError.value = ''
  detail.value = row
  Object.assign(reply, {
    status: row.status === 'pending' ? 'processing' : row.status,
    reply: row.reply || '',
  })
  try {
    let value = row
    if (props.section === 'interactions')
      value = await operationsApi.interaction(row.id, ops.context)
    if (props.section === 'reports') value = await operationsApi.report(row.id, ops.context)
    if (props.section === 'feedback' && row.interaction_id) {
      const interaction = await operationsApi.interaction(row.interaction_id, ops.context)
      value = {
        ...row,
        question: interaction.question,
        sql: interaction.sql,
        datasource_id: interaction.datasource_id,
        error_summary: interaction.error_summary,
      }
    }
    if (version === detailVersion) detail.value = value
  } catch {
    if (version === detailVersion) detailError.value = '详情加载失败，请关闭后重试'
  } finally {
    if (version === detailVersion) detailLoading.value = false
  }
}
async function saveReply() {
  if (!detail.value || detailLoading.value || actionLoading.value) return
  if (['resolved', 'declined'].includes(reply.status) && !reply.reply.trim()) {
    ElMessage.warning('请填写处理说明')
    return
  }
  actionLoading.value = true
  try {
    await operationsApi.resolveFeedback(detail.value.id, reply, ops.context)
    ElMessage.success('处理结果已保存')
    detailOpen.value = false
    await load()
  } finally {
    actionLoading.value = false
  }
}
function viewUser(row: OperationRow) {
  router.push({
    path: '/system/operations/interactions',
    query: { user_id: String(row.user_id), auth_source: row.auth_source },
  })
}
async function createReport() {
  actionLoading.value = true
  try {
    if (!ops.tenantId) {
      ElMessage.warning('请选择租户或工作空间')
      return
    }
    const dates = dateQuery(dateRange.value)
    if (dates.date_to && new Date(dates.date_to).getTime() > Date.now())
      dates.date_to = new Date().toISOString()
    await operationsApi.createReport({ ...ops.context, ...dates })
    ElMessage.success('报告任务已创建，可刷新查看进度')
    await load(true)
  } finally {
    actionLoading.value = false
  }
}
async function retryReport(row: OperationRow) {
  actionLoading.value = true
  try {
    await operationsApi.retryReport(row.id, ops.context)
    ElMessage.success('已重新提交报告任务')
    await load()
  } finally {
    actionLoading.value = false
  }
}
function editGrant(row?: OperationRow) {
  Object.assign(grant, {
    tenant_id: String(row?.tenant_id ?? ops.tenantId),
    user_id: String(row?.user_id ?? ''),
    account: row?.account || '',
    enabled: row?.enabled ?? true,
  })
  grantOpen.value = true
}
async function saveGrant() {
  if (!grant.tenant_id.trim() || !grant.user_id.trim() || !grant.account.trim()) {
    ElMessage.warning('请填写租户、用户 ID 和账号')
    return
  }
  actionLoading.value = true
  try {
    await operationsApi.grant(grant)
    ElMessage.success('授权已保存')
    grantOpen.value = false
    await load()
  } finally {
    actionLoading.value = false
  }
}
function createDraft(kind: 'terminology' | 'data-training', source: OperationRow) {
  const tenantId = String(source.tenant_id ?? detail.value?.tenant_id ?? ops.tenantId)
  const scope = (source.scope ?? detail.value?.scope ?? ops.scope) as 'local' | 'platform'
  const fromReport = props.section === 'reports'
  const values: OperationRow =
    kind === 'terminology'
      ? {
          word: source.word || source.title || '',
          description:
            source.description || source.content || source.expected || source.reply || '',
          other_words: source.other_words || [''],
          specific_ds: false,
          datasource_ids: [],
          enabled: false,
        }
      : {
          question: source.question || source.title || detail.value?.question || '',
          description: source.sql || (fromReport ? source.content || source.description : '') || '',
          datasource: source.datasource_id || null,
          enabled: false,
        }
  if (fromReport && source.draft_id) {
    values.report_id = detail.value?.id
    values.draft_id = source.draft_id
  }
  ops.draft = { kind, scope, tenantId, values }
  detailOpen.value = false
  router.push(
    kind === 'terminology' ? '/system/setting/terminology' : '/system/setting/sql-examples'
  )
}
watch(
  () => [props.section, route.query.user_id, route.query.auth_source],
  () => {
    detailOpen.value = false
    detailVersion++
    filters.user_id = String(route.query.user_id || '')
    filters.auth_source = String(route.query.auth_source || '')
    filters.status = ''
    filters.outcome = ''
    load(true)
  }
)
function changeContext() {
  filters.user_id = ''
  filters.auth_source = ''
  detailOpen.value = false
  detailVersion++
  load(true)
}
onMounted(() => {
  filters.user_id = String(route.query.user_id || '')
  filters.auth_source = String(route.query.auth_source || '')
  load()
})
</script>

<template>
  <section class="operations-page">
    <header>
      <h2>{{ title }}</h2>
      <el-button :loading="loading" @click="load()">刷新</el-button>
    </header>
    <OperationsContext v-if="section !== 'grants'" @change="changeContext" />
    <el-form class="operations-filters" inline @submit.prevent="load(true)">
      <el-form-item v-if="section !== 'grants'"
        ><el-date-picker
          v-model="dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          style="max-width: 100%; width: 280px"
      /></el-form-item>
      <el-form-item v-if="!['reports', 'grants'].includes(section)"
        ><el-input
          v-model="filters.keyword"
          clearable
          placeholder="关键词"
          @keyup.enter="load(true)"
      /></el-form-item>
      <el-form-item v-if="section === 'interactions' || section === 'feedback'"
        ><el-input v-model="filters.user_id" clearable placeholder="用户 ID"
      /></el-form-item>
      <el-form-item v-if="['interactions', 'feedback'].includes(section)">
        <el-select v-model="filters.status" clearable placeholder="全部状态" style="width: 150px">
          <template v-if="section === 'feedback'"
            ><el-option
              v-for="item in feedbackStatuses"
              :key="item.value"
              :label="item.label"
              :value="item.value"
          /></template>
          <template v-else-if="section === 'interactions'"
            ><el-option
              v-for="item in interactionStatuses"
              :key="item"
              :label="statusLabel(item)"
              :value="item"
          /></template>
          <template v-else
            ><el-option
              v-for="item in ['queued', 'running', 'completed', 'failed', 'interrupted']"
              :key="item"
              :label="statusLabel(item)"
              :value="item"
          /></template>
        </el-select>
      </el-form-item>
      <el-form-item v-if="['overview', 'users', 'interactions'].includes(section)">
        <el-select v-model="filters.outcome" clearable placeholder="全部查询结果" style="width: 180px">
          <el-option
            v-for="item in queryResultOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item v-if="!['reports', 'grants'].includes(section)"
        ><el-button type="primary" native-type="submit">查询</el-button
        ><el-button @click="reset">重置</el-button></el-form-item
      >
      <el-form-item v-if="section === 'reports'"
        ><span class="hint" style="margin-right: 12px">未选日期时分析最近 7 天</span
        ><el-button :loading="actionLoading" @click="createReport"
          >生成归因报告</el-button
        ></el-form-item
      >
      <el-form-item v-if="section === 'grants' && ops.access.instance_admin"
        ><el-button @click="editGrant()">添加授权</el-button></el-form-item
      >
    </el-form>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <div v-if="section === 'overview'" v-loading="loading">
      <div class="metric-grid">
        <article v-for="card in overviewCards" :key="card.label">
          <span>{{ card.label }}</span
          ><strong>{{ card.value }}</strong>
        </article>
      </div>
      <el-alert
        title="耗时包含全部执行。执行成功不代表业务口径正确；全为空值不代表底层没有匹配数据，数值 0 属于有结果。旧记录不追溯推断。"
        type="info"
        :closable="false"
      />
      <div class="overview-detail" v-if="summary.status_counts">
        <h3>问数状态分布</h3>
        <pre>{{ text(summary.status_counts) }}</pre>
      </div>
    </div>
    <el-table
      v-else
      v-loading="loading"
      :data="rows"
      class="operations-table"
      empty-text="当前范围暂无记录"
    >
      <template v-if="section === 'users'">
        <el-table-column label="用户" min-width="160"
          ><template #default="{ row }">{{
            row.user_name || row.name || row.account || row.user_id
          }}</template></el-table-column
        >
        <el-table-column prop="account" label="账号" min-width="130" />
        <el-table-column label="问数次数" width="110"
          ><template #default="{ row }">{{ row.questions ?? '—' }}</template></el-table-column
        >
        <el-table-column label="失败次数" width="110"
          ><template #default="{ row }">{{ row.errors ?? '—' }}</template></el-table-column
        >
        <el-table-column label="平均耗时" width="130"
          ><template #default="{ row }">{{
            displayDuration(row.avg_duration_ms)
          }}</template></el-table-column
        >
        <el-table-column label="最近使用" min-width="180"
          ><template #default="{ row }">{{ displayTime(row.last_seen) }}</template></el-table-column
        >
        <el-table-column label="操作" width="110"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="viewUser(row)">问数明细</el-button></template
          ></el-table-column
        >
      </template>
      <template v-if="section === 'interactions'">
        <el-table-column label="时间" min-width="170"
          ><template #default="{ row }">{{
            displayTime(row.created_at)
          }}</template></el-table-column
        >
        <el-table-column label="用户" min-width="120"
          ><template #default="{ row }">{{
            row.user_name || row.account || row.user_id
          }}</template></el-table-column
        >
        <el-table-column prop="question" label="问题" min-width="250" show-overflow-tooltip />
        <el-table-column label="状态" width="100"
          ><template #default="{ row }"
            ><el-tag
              :type="
                ['error', 'timeout', 'permission_denied'].includes(row.status) ? 'danger' : 'info'
              "
              >{{ statusLabel(row.status) }}</el-tag
            ></template
          ></el-table-column
        >
        <el-table-column label="耗时" width="125"
          ><template #default="{ row }">{{ rowDuration(row) }}</template></el-table-column
        >
        <el-table-column label="查询结果" width="150"
          ><template #default="{ row }">{{ queryResultLabel(row.outcome) }}</template></el-table-column
        >
        <el-table-column label="轮次" width="80"
          ><template #default="{ row }">{{
            row.round_count ?? row.round_index ?? row.turn_index ?? '—'
          }}</template></el-table-column
        >
        <el-table-column label="操作" width="85"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="showDetail(row)">详情</el-button></template
          ></el-table-column
        >
      </template>
      <template v-if="section === 'feedback'">
        <el-table-column label="用户" min-width="110"
          ><template #default="{ row }">{{
            row.user_name || row.account || row.user_id
          }}</template></el-table-column
        >
        <el-table-column label="类型" width="120"
          ><template #default="{ row }">{{
            feedbackTypeLabel(row.type)
          }}</template></el-table-column
        >
        <el-table-column prop="content" label="反馈内容" min-width="240" show-overflow-tooltip />
        <el-table-column label="状态" width="110"
          ><template #default="{ row }"
            ><el-tag>{{ statusLabel(row.status) }}</el-tag></template
          ></el-table-column
        >
        <el-table-column label="提交时间" min-width="170"
          ><template #default="{ row }">{{
            displayTime(row.created_at)
          }}</template></el-table-column
        >
        <el-table-column label="操作" width="90"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="showDetail(row)">处理</el-button></template
          ></el-table-column
        >
      </template>
      <template v-if="section === 'reports'">
        <el-table-column label="报告期间" min-width="210"
          ><template #default="{ row }"
            >{{ displayTime(row.range_start) }} ～ {{ displayTime(row.range_end) }}</template
          ></el-table-column
        >
        <el-table-column label="状态" width="110"
          ><template #default="{ row }"
            ><el-tag>{{ statusLabel(row.status) }}</el-tag></template
          ></el-table-column
        >
        <el-table-column label="处理进度" min-width="180"
          ><template #default="{ row }"
            >{{ row.progress_json?.completed_batches ?? 0 }} /
            {{ row.progress_json?.total_batches ?? 0 }} 批次</template
          ></el-table-column
        >
        <el-table-column label="创建时间" min-width="170"
          ><template #default="{ row }">{{
            displayTime(row.created_at)
          }}</template></el-table-column
        >
        <el-table-column label="操作" width="140"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="showDetail(row)">查看</el-button
            ><el-button
              v-if="['failed', 'interrupted'].includes(row.status)"
              link
              type="primary"
              :loading="actionLoading"
              @click="retryReport(row)"
              >重试</el-button
            ></template
          ></el-table-column
        >
      </template>
      <template v-if="section === 'grants'">
        <el-table-column prop="tenant_id" label="租户 ID" min-width="140" /><el-table-column
          prop="user_id"
          label="用户 ID"
          min-width="130"
        /><el-table-column prop="account" label="账号" min-width="180" />
        <el-table-column label="状态" width="100"
          ><template #default="{ row }"
            ><el-tag :type="row.enabled ? 'success' : 'info'">{{
              row.enabled ? '已授权' : '已停用'
            }}</el-tag></template
          ></el-table-column
        >
        <el-table-column label="操作" width="90"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="editGrant(row)">调整</el-button></template
          ></el-table-column
        >
      </template>
    </el-table>
    <el-pagination
      v-if="!['overview', 'grants'].includes(section) && total"
      v-model:current-page="page"
      v-model:page-size="size"
      :page-sizes="[20, 50, 100]"
      :total="total"
      layout="total, sizes, prev, pager, next"
      @current-change="load()"
      @size-change="load(true)"
    />
    <el-drawer
      v-model="detailOpen"
      :title="section === 'feedback' ? '反馈处理' : section === 'reports' ? '归因报告' : '问数详情'"
      size="min(840px, 96vw)"
      append-to-body
      destroy-on-close
      :close-on-click-modal="!actionLoading"
      :close-on-press-escape="!actionLoading"
      :show-close="!actionLoading"
      @closed="detailVersion++"
    >
      <div v-loading="detailLoading" v-if="detail" class="operation-detail">
        <el-alert v-if="detailError" :title="detailError" type="warning" :closable="false" />
        <template v-if="section === 'feedback'">
          <el-descriptions :column="1" border
            ><el-descriptions-item label="用户">{{
              detail.user_name || detail.account || detail.user_id
            }}</el-descriptions-item
            ><el-descriptions-item label="问题">{{
              detail.question || (detail.interaction_id ? '关联问答尚未加载' : '未关联具体问答')
            }}</el-descriptions-item
            ><el-descriptions-item label="反馈内容">{{ detail.content }}</el-descriptions-item
            ><el-descriptions-item label="期望结果">{{
              detail.expected || '—'
            }}</el-descriptions-item></el-descriptions
          >
          <template v-if="detail.error_summary"
            ><h3>错误信息</h3>
            <pre>{{ detail.error_summary }}</pre>
          </template>
          <el-form
            label-position="top"
            :disabled="detailLoading || actionLoading"
            @submit.prevent="saveReply"
            ><el-form-item label="处理状态"
              ><el-select v-model="reply.status"
                ><el-option
                  v-for="item in feedbackStatuses"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value" /></el-select></el-form-item
            ><el-form-item label="处理说明（反馈人可见）"
              ><el-input
                v-model="reply.reply"
                type="textarea"
                :rows="4"
                maxlength="2000"
                show-word-limit /></el-form-item
            ><el-button type="primary" :loading="actionLoading" native-type="submit"
              >保存处理结果</el-button
            ></el-form
          >
          <div class="draft-actions">
            <span>需要完善问数知识时：</span
            ><el-button :disabled="detailLoading" @click="createDraft('terminology', detail)"
              >转为术语草稿</el-button
            ><el-button :disabled="detailLoading" @click="createDraft('data-training', detail)"
              >转为 SQL 示例草稿</el-button
            >
          </div>
        </template>
        <template v-else-if="section === 'reports'">
          <el-tag>{{ statusLabel(detail.status) }}</el-tag>
          <p>
            分析范围：{{ displayTime(detail.range_start) }} ～ {{ displayTime(detail.range_end) }}
          </p>
          <p>
            已完成 {{ detail.progress_json?.completed_batches ?? 0 }} /
            {{ detail.progress_json?.total_batches ?? 0 }} 批次
          </p>
          <el-alert
            v-if="detail.progress_json?.error"
            :title="text(detail.progress_json.error)"
            type="error"
            :closable="false"
          />
          <el-empty
            v-if="!reportFindings.length"
            description="暂无分析结论，请稍后刷新或查看失败原因"
          />
          <article v-for="(finding, index) in reportFindings" :key="index" class="suggestion">
            <h3>{{ finding.theme }}</h3>
            <p>{{ finding.possible_cause }}</p>
            <p v-if="finding.uncertainty" class="hint">判断限制：{{ finding.uncertainty }}</p>
            <p>建议：{{ finding.suggestion }}</p>
            <ul v-if="finding.evidence?.length">
              <li v-for="(evidence, itemIndex) in finding.evidence" :key="itemIndex">
                记录 {{ evidence.interaction_id }}：{{ evidence.observation }}
              </li>
            </ul>
          </article>
          <h3 v-if="reportDrafts.length">建议草稿</h3>
          <article
            v-for="(draft, index) in reportDrafts"
            :key="draft.draft_id || index"
            class="suggestion"
          >
            <h4>{{ draft.title }}</h4>
            <pre>{{ text(draft.content) }}</pre>
            <el-tag v-if="draft.status === 'accepted'" type="success">已保存为知识草稿</el-tag>
            <el-button
              v-else-if="draft.kind === 'terminology'"
              @click="createDraft('terminology', draft)"
              >创建术语草稿</el-button
            >
            <el-button
              v-else-if="draft.kind === 'sql_example'"
              @click="createDraft('data-training', draft)"
              >创建 SQL 示例草稿</el-button
            >
          </article>
          <p class="hint">建议需人工核对，创建草稿不会自动启用知识。</p>
        </template>
        <template v-else>
          <el-descriptions :column="1" border
            ><el-descriptions-item label="问题">{{ detail.question }}</el-descriptions-item
            ><el-descriptions-item label="用户">{{
              detail.user_name || detail.account || detail.user_id
            }}</el-descriptions-item
            ><el-descriptions-item label="状态">{{
              statusLabel(detail.status)
            }}</el-descriptions-item
            ><el-descriptions-item label="耗时">{{ rowDuration(detail) }}</el-descriptions-item
            ><el-descriptions-item label="查询结果">{{ queryResultLabel(detail.outcome) }}</el-descriptions-item
            ><el-descriptions-item label="轮次">{{
              detail.round_count ?? detail.round_index ?? detail.turn_index ?? '—'
            }}</el-descriptions-item></el-descriptions
          >
          <template v-if="detail.error_summary || detail.error"
            ><h3>错误信息</h3>
            <pre>{{ text(detail.error_summary || detail.error) }}</pre>
          </template>
          <template v-if="detail.sql"
            ><h3>生成 SQL</h3>
            <pre>{{ detail.sql }}</pre>
          </template>
          <template v-if="detail.stages_json"
            ><h3>执行阶段</h3>
            <el-table :data="detail.stages_json"
              ><el-table-column label="阶段" min-width="150"
                ><template #default="{ row }">{{
                  stageLabel(row)
                }}</template></el-table-column
              ><el-table-column label="耗时" width="120"
                ><template #default="{ row }">{{ rowDuration(row) }}</template></el-table-column
              ><el-table-column label="错误" min-width="200"
                ><template #default="{ row }">{{
                  text(row.error_summary || row.error)
                }}</template></el-table-column
              ></el-table
            ></template
          >
          <template v-if="detail.records"
            ><h3>交互记录</h3>
            <pre>{{ text(detail.records) }}</pre>
          </template>
        </template>
      </div>
    </el-drawer>
    <el-dialog
      v-model="grantOpen"
      title="运营授权"
      width="min(520px, 94vw)"
      append-to-body
      :close-on-click-modal="!actionLoading"
      :close-on-press-escape="!actionLoading"
      :show-close="!actionLoading"
    >
      <p class="hint">仅授予问数运营权限，不增加模型配置、用户管理或业务数据权限。</p>
      <el-form label-position="top" :disabled="actionLoading" @submit.prevent="saveGrant"
        ><el-form-item label="平台租户 ID" required
          ><el-input v-model="grant.tenant_id" /></el-form-item
        ><el-form-item label="平台用户 ID" required
          ><el-input v-model="grant.user_id" /></el-form-item
        ><el-form-item label="账号" required><el-input v-model="grant.account" /></el-form-item
        ><el-form-item label="允许问数运营"><el-switch v-model="grant.enabled" /></el-form-item
        ><el-button type="primary" native-type="submit" :loading="actionLoading"
          >保存</el-button
        ></el-form
      >
    </el-dialog>
  </section>
</template>

<style scoped>
.operations-page {
  height: 100%;
  overflow: auto;
  padding: 20px 24px 76px;
  box-sizing: border-box;
  min-width: 0;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}
h2 {
  font-size: 22px;
  margin: 0;
}
.operations-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0 12px;
}
.operations-filters :deep(.ed-form-item) {
  margin-right: 0;
  margin-bottom: 12px;
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 14px;
  margin-bottom: 20px;
}
.metric-grid article {
  border: 1px solid #e2e6ea;
  border-radius: 8px;
  padding: 18px;
  background: #fff;
}
.metric-grid span {
  color: #646a73;
  display: block;
}
.metric-grid strong {
  display: block;
  margin-top: 12px;
  font-size: 25px;
}
.operations-table {
  width: 100%;
  margin: 14px 0;
}
.operation-detail {
  overflow-wrap: anywhere;
}
.operation-detail .ed-form {
  margin-top: 20px;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: inherit;
  background: #f6f8fa;
  padding: 14px;
  border-radius: 6px;
}
.hint {
  color: #646a73;
  font-size: 13px;
}
.draft-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 22px;
}
.suggestion {
  border-bottom: 1px solid #e2e6ea;
  padding: 12px 0;
}
@media (max-width: 640px) {
  .operations-page {
    padding: 16px 12px 76px;
  }
  .operations-filters :deep(.ed-form-item) {
    max-width: 100%;
  }
  .operations-page :deep(.ed-pagination) {
    overflow-x: auto;
  }
}
</style>
