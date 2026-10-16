<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { constraintsApi, type QuestionConstraint } from '@/api/constraints'

const loading = ref(false)
const error = ref('')
const rows = ref<QuestionConstraint[]>([])
const saving = reactive<Record<string, boolean>>({})

async function load() {
  loading.value = true
  error.value = ''
  try {
    rows.value = await constraintsApi.list()
  } catch {
    error.value = '问数约束加载失败，请确认管理员权限后重试。'
  } finally {
    loading.value = false
  }
}

async function save(row: QuestionConstraint) {
  if (!row.content.trim()) {
    ElMessage.warning('请填写约束内容')
    return
  }
  saving[row.code] = true
  try {
    const result = await constraintsApi.update(row.code, {
      content: row.content.trim(),
      enabled: row.enabled,
    })
    Object.assign(row, result)
    ElMessage.success('已保存，启用的约束将在后续问数中生效')
  } finally {
    saving[row.code] = false
  }
}

onMounted(load)
</script>

<template>
  <section v-loading="loading" class="question-constraints">
    <h2>问数约束</h2>
    <p class="description">维护通用的选表、指标、日期与结果解释规则，对本实例后续问数生效。</p>
    <el-alert type="info" :closable="false" show-icon>
      五条初始约束默认停用。编辑或切换启用后点击对应的“保存”；升级会保留修改。约束不会扩大数据查询权限。
    </el-alert>
    <el-alert v-if="error" class="load-error" type="error" :closable="false" :title="error">
      <el-button link type="primary" @click="load">重试</el-button>
    </el-alert>
    <el-card v-for="row in rows" :key="row.code" class="constraint-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span class="rule-name">{{ row.name }}</span>
          <el-switch v-model="row.enabled" :disabled="saving[row.code]" active-text="启用" />
        </div>
      </template>
      <el-input
        v-model="row.content"
        type="textarea"
        :rows="4"
        :maxlength="2000"
        show-word-limit
        :disabled="saving[row.code]"
        :aria-label="`${row.name}内容`"
      />
      <div class="card-actions">
        <el-button type="primary" :loading="saving[row.code]" @click="save(row)">保存</el-button>
      </div>
    </el-card>
  </section>
</template>

<style scoped lang="less">
.question-constraints {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  box-sizing: border-box;
  padding: 24px;
  h2 { margin: 0 0 12px; }
}
.description { color: var(--el-text-color-secondary); margin: 0 0 16px; }
.constraint-card, .load-error { margin-top: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.rule-name { font-weight: 600; }
.card-actions { display: flex; justify-content: flex-end; margin-top: 12px; }
</style>
