<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAssistantStore } from '@/stores/assistant.ts'

const props = defineProps<{
  error?: string
}>()

const { t } = useI18n()

interface PermissionDeniedTable {
  name?: string
  displayName?: string
  catalogPath?: string
  reason?: string
  applyUrl?: string
  tableId?: string | number
  categoryId?: string | number
  categoryName?: string
}

interface ParsedErrorMessage {
  message?: string
  showMore: boolean
  traceback?: string
  type?: string
  gate?: string
  reason?: string
  datasourceId?: string | number
  deniedTables?: PermissionDeniedTable[]
}

const assistantStore = useAssistantStore()
const isCompletePage = computed(() => !assistantStore.getAssistant || assistantStore.getEmbedded)

const showBlock = computed(() => {
  return props.error && props.error?.trim().length > 0
})

const errorMessage = computed<ParsedErrorMessage>(() => {
  const obj: ParsedErrorMessage = {
    message: props.error,
    showMore: false,
    traceback: '',
  }
  if (showBlock.value && props.error?.trim().startsWith('{') && props.error?.trim().endsWith('}')) {
    try {
      const json = JSON.parse(props.error?.trim())
      obj.message = json['message'] ?? json['content'] ?? props.error
      obj.traceback = json['traceback']
      obj.type = json['type']
      obj.gate = json['gate']
      obj.reason = json['reason']
      obj.datasourceId = json['datasourceId']
      obj.deniedTables = Array.isArray(json['deniedTables']) ? json['deniedTables'] : []
      if ((obj.traceback?.trim().length ?? 0) > 0) {
        obj.showMore = true
      }
    } catch (e) {
      console.error(e)
    }
  }
  return obj
})

const show = ref(false)

const permissionDeniedTables = computed(() => errorMessage.value.deniedTables ?? [])

function getTableDisplayName(table: PermissionDeniedTable) {
  return table.displayName || table.catalogPath || table.name || '未知表'
}

function getPermissionApplyPayload(table: PermissionDeniedTable) {
  return {
    authScope: 'category',
    resourceIds: table.categoryId ? [table.categoryId] : [],
    tableId: table.tableId,
    tableName: table.name,
    categoryId: table.categoryId,
    categoryName: table.categoryName || table.displayName || table.catalogPath,
    applyUrl: table.applyUrl,
  }
}

function requestPermissionApply(table: PermissionDeniedTable) {
  if (!table.categoryId) {
    return false
  }
  const payload = getPermissionApplyPayload(table)
  const qiankunProps = window.__SQLBOT_QIANKUN_PROPS__

  if (typeof qiankunProps?.sendToMain === 'function') {
    qiankunProps.sendToMain('applyPermission', payload)
    return true
  }
  window.dispatchEvent(
    new CustomEvent('sqlbot-message', {
      detail: {
        type: 'applyPermission',
        data: payload,
      },
    })
  )
  return !!window.__POWERED_BY_QIANKUN__
}

function openApplyUrl(table: PermissionDeniedTable) {
  if (requestPermissionApply(table)) {
    return
  }
  if (!table.applyUrl) {
    return
  }
  window.open(table.applyUrl, '_blank')
}

function showTraceBack() {
  show.value = true
}
</script>

<template>
  <div v-if="showBlock">
    <div
      v-if="errorMessage.type === 'permission_denied'"
      class="error-container permission-denied"
    >
      <div class="permission-title">当前账号暂无相关表查询权限</div>
      <div class="permission-desc">申请权限通过后，再重新发起问数。</div>
      <div v-if="permissionDeniedTables.length > 0" class="permission-table-list">
        <div
          v-for="table in permissionDeniedTables"
          :key="`${table.name || ''}-${table.applyUrl || ''}`"
          class="permission-table-item"
        >
          <div class="permission-table-info">
            <div class="permission-table-name">{{ getTableDisplayName(table) }}</div>
            <div class="permission-table-meta">
              <span v-if="table.name && table.name !== getTableDisplayName(table)">
                {{ table.name }}
              </span>
              <span v-if="table.reason">{{ table.reason }}</span>
            </div>
          </div>
          <el-button v-if="table.applyUrl" size="small" type="primary" @click="openApplyUrl(table)">
            申请权限
          </el-button>
        </div>
      </div>
      <div v-else class="permission-table-empty">
        权限服务未返回具体无权表，请联系数据管理员确认权限范围。
      </div>
    </div>
    <div
      v-else-if="!errorMessage.showMore && errorMessage.type == undefined"
      v-dompurify-html="errorMessage.message"
      class="error-container"
    ></div>
    <div v-else class="error-container row">
      <template v-if="errorMessage.type === 'db-connection-err'">
        {{ t('chat.ds_is_invalid') }}
      </template>
      <template v-else-if="errorMessage.type === 'exec-sql-err'">
        {{ t('chat.exec-sql-err') }}
      </template>
      <template v-else>
        {{ t('chat.error') }}
      </template>
      <el-button v-if="errorMessage.showMore" text @click="showTraceBack">
        {{ t('chat.show_error_detail') }}
      </el-button>
    </div>

    <el-drawer
      v-model="show"
      :size="!isCompletePage ? '100%' : '600px'"
      :title="t('chat.error')"
      direction="rtl"
      body-class="chart-sql-error-body"
    >
      <el-main>
        <div v-dompurify-html="errorMessage.traceback" class="error-container open"></div>
      </el-main>
    </el-drawer>
  </div>
</template>

<style lang="less">
.chart-sql-error-body {
  padding: 0;
}
</style>
<style scoped lang="less">
.error-container {
  font-weight: 400;
  font-size: 16px;
  line-height: 24px;
  color: rgba(31, 35, 41, 1);
  white-space: pre-wrap;
  word-break: break-word;

  &.row {
    display: flex;
    flex-direction: row;
    align-items: center;
  }
  &.open {
    font-size: 14px;
    line-height: 20px;
  }
}

.permission-denied {
  border: 1px solid #f2c5c5;
  background: #fff7f7;
  border-radius: 6px;
  padding: 12px 14px;
}

.permission-title {
  font-weight: 600;
  color: #b42318;
}

.permission-desc {
  margin-top: 4px;
  font-size: 14px;
  line-height: 20px;
  color: #5f6368;
}

.permission-table-list {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.permission-table-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px;
  background: #ffffff;
  border: 1px solid #f0dada;
  border-radius: 6px;
}

.permission-table-info {
  min-width: 0;
}

.permission-table-name {
  font-size: 14px;
  line-height: 20px;
  font-weight: 600;
  color: #1f2329;
  overflow-wrap: anywhere;
}

.permission-table-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 2px;
  font-size: 12px;
  line-height: 18px;
  color: #646a73;
}

.permission-table-empty {
  margin-top: 8px;
  font-size: 14px;
  color: #646a73;
}
</style>
