<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useOperationsStore } from '@/stores/operations'
const ops = useOperationsStore()
const emit = defineEmits<{ change: [] }>()
const tenants = computed(() =>
  ops.tenants.filter((item) => !item.scope || item.scope === ops.scope)
)
function changeScope() {
  ops.tenantId = ''
  emit('change')
}
onMounted(() => {
  ops.loadTenants()
})
</script>

<template>
  <div class="operations-context">
    <template v-if="ops.access.instance_admin">
      <el-select
        v-model="ops.scope"
        aria-label="数据范围"
        style="width: 150px"
        @change="changeScope"
      >
        <el-option label="平台租户" value="platform" />
        <el-option label="本地工作空间" value="local" />
      </el-select>
      <el-select
        v-model="ops.tenantId"
        aria-label="租户或工作空间"
        filterable
        clearable
        placeholder="选择租户或工作空间"
        style="width: 230px"
        @change="emit('change')"
      >
        <el-option
          v-for="tenant in tenants"
          :key="String(tenant.tenant_id ?? tenant.id)"
          :value="String(tenant.tenant_id ?? tenant.id)"
          :label="tenant.name || tenant.tenant_name || String(tenant.tenant_id ?? tenant.id)"
        />
      </el-select>
    </template>
    <el-tag v-else type="info">当前租户 {{ ops.tenantId }}</el-tag>
    <slot />
  </div>
</template>

<style scoped>
.operations-context {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}
</style>
