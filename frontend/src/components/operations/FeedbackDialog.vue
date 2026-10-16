<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { operationsApi, type OperationRow } from '@/api/operations'
import { useOperationsStore } from '@/stores/operations'
import {
  feedbackTypes,
  feedbackTypeLabel,
  statusLabel,
  makeSubmissionId,
  displayTime,
} from '@/views/system/operations/operationsPolicy'
const ops = useOperationsStore()
const saving = ref(false)
const loading = ref(false)
const history = ref<OperationRow[]>([])
const total = ref(0)
const page = ref(1)
const form = reactive({ type: 'incorrect', content: '', expected: '', submission_id: '' })
let historyRequestVersion = 0
async function loadHistory() {
  const version = ++historyRequestVersion
  loading.value = true
  try {
    const result = await operationsApi.myFeedback({
      record_id: ops.feedbackRecordId,
      page: page.value,
      size: 10,
    })
    if (version !== historyRequestVersion || !ops.feedbackOpen) return
    history.value = result.items
    total.value = result.total
  } finally {
    if (version === historyRequestVersion) loading.value = false
  }
}
watch(
  () => [ops.feedbackOpen, ops.feedbackRecordId, ops.feedbackInteractionId],
  ([open]) => {
    historyRequestVersion++
    history.value = []
    total.value = 0
    loading.value = false
    if (!open) return
    Object.assign(form, {
      type: 'incorrect',
      content: '',
      expected: '',
      submission_id: makeSubmissionId(),
    })
    page.value = 1
    loadHistory()
  }
)
async function submit() {
  if (saving.value) return
  if (!form.content.trim()) {
    ElMessage.warning('请填写问题说明')
    return
  }
  saving.value = true
  try {
    await operationsApi.submitFeedback({
      ...form,
      record_id: ops.feedbackRecordId,
      interaction_id: ops.feedbackInteractionId,
    })
    ElMessage.success('反馈已提交')
    ops.feedbackTab = 'mine'
    await loadHistory()
    form.content = ''
    form.expected = ''
    form.submission_id = makeSubmissionId()
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="ops.feedbackOpen"
    title="问题反馈"
    width="min(620px, 94vw)"
    append-to-body
    destroy-on-close
    :close-on-click-modal="!saving"
    :close-on-press-escape="!saving"
    :show-close="!saving"
  >
    <el-tabs v-model="ops.feedbackTab">
      <el-tab-pane label="提交反馈" name="submit">
        <p class="hint">
          {{
            ops.feedbackRecordId || ops.feedbackInteractionId
              ? '已关联当前问答，无需复制问题和错误信息。'
              : '可反馈问数使用中的问题或改进建议。'
          }}
        </p>
        <el-form label-position="top" :disabled="saving" @submit.prevent="submit">
          <el-form-item label="问题类型"
            ><el-select v-model="form.type" style="width: 100%"
              ><el-option
                v-for="item in feedbackTypes"
                :key="item.value"
                :label="item.label"
                :value="item.value" /></el-select
          ></el-form-item>
          <el-form-item label="问题说明" required
            ><el-input
              v-model="form.content"
              type="textarea"
              :rows="4"
              maxlength="2000"
              show-word-limit
              placeholder="哪里不符合预期？"
          /></el-form-item>
          <el-form-item label="期望结果（可选）"
            ><el-input
              v-model="form.expected"
              type="textarea"
              :rows="2"
              maxlength="2000"
              show-word-limit
          /></el-form-item>
          <el-button type="primary" :loading="saving" native-type="submit">提交反馈</el-button>
        </el-form>
      </el-tab-pane>
      <el-tab-pane label="我的反馈" name="mine">
        <div v-loading="loading" class="feedback-history">
          <el-empty v-if="!history.length" description="暂无反馈" />
          <article v-for="item in history" :key="item.id">
            <div class="feedback-heading">
              <strong>{{ feedbackTypeLabel(item.type) }}</strong
              ><el-tag>{{ statusLabel(item.status) }}</el-tag>
            </div>
            <p>{{ item.content }}</p>
            <p v-if="item.reply" class="reply">处理回复：{{ item.reply }}</p>
            <small>{{ displayTime(item.created_at) }}</small>
          </article>
        </div>
        <el-pagination
          v-if="total > 10"
          v-model:current-page="page"
          :page-size="10"
          layout="prev, pager, next"
          :total="total"
          @current-change="loadHistory"
        />
      </el-tab-pane>
    </el-tabs>
  </el-dialog>
</template>

<style scoped>
.hint,
small {
  color: #646a73;
}
.feedback-history {
  max-height: 55vh;
  overflow: auto;
}
.feedback-history article {
  border-bottom: 1px solid #e5e8eb;
  padding: 12px 0;
}
.feedback-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.reply {
  background: #f4f7fa;
  padding: 10px;
  border-radius: 6px;
}
</style>
