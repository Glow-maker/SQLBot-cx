<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import OperationsContext from './OperationsContext.vue'
import { useOperationsStore } from '@/stores/operations'
import { operationsApi } from '@/api/operations'
import { knowledgeScopeReady } from '@/api/knowledge'
const ops = useOperationsStore()
const emit = defineEmits<{ change: [] }>()
const refreshing = ref(false)
const canRefresh = ref(false)
const count = ref<number | null>(null)
let requestVersion = 0
async function load() {
  const version = ++requestVersion
  count.value = null
  canRefresh.value = false
  if (!knowledgeScopeReady()) return
  try {
    const catalog = await operationsApi.catalog(ops.context)
    if (version !== requestVersion) return
    canRefresh.value = catalog.can_refresh === true
    count.value = catalog.sources?.length || 0
  } catch {
    /* The page's data request displays failures. */
  }
}
async function refresh() {
  refreshing.value = true
  try {
    await operationsApi.refreshCatalog(ops.context)
    ElMessage.success('目录已刷新')
    await load()
    emit('change')
  } finally {
    refreshing.value = false
  }
}
watch(() => [ops.scope, ops.tenantId], load)
onMounted(load)
</script>

<template>
  <div class="knowledge-context">
    <OperationsContext @change="emit('change')"
      ><el-button v-if="canRefresh" :loading="refreshing" @click="refresh"
        >刷新目录</el-button
      ></OperationsContext
    >
    <el-alert
      v-if="!knowledgeScopeReady()"
      title="请选择一个租户后维护知识。"
      type="info"
      :closable="false"
    />
    <el-alert
      v-else-if="count === 0 && ops.scope === 'platform'"
      title="当前租户尚无可用目录；请平台运营人员刷新已授权目录。"
      type="info"
      :closable="false"
    />
  </div>
</template>

<style scoped>
.knowledge-context {
  margin-bottom: 16px;
}
</style>
