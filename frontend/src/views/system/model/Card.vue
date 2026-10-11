<script lang="ts" setup>
import delIcon from '@/assets/svg/icon_delete.svg'
import icon_admin_outlined from '@/assets/svg/icon_admin_outlined.svg'
import edit from '@/assets/svg/icon_edit_outlined.svg'
import { get_supplier } from '@/entity/supplier'
import { computed, ref } from 'vue'
const props = withDefaults(
  defineProps<{
    name: string
    modelType: string
    baseModel: string
    id?: string
    isDefault?: boolean
    supplier?: number
  }>(),
  {
    name: '-',
    modelType: '-',
    baseModel: '-',
    id: '-',
    isDefault: false,
    supplier: 0,
  }
)
const errorMsg = ref('')
const current_supplier = computed(() => {
  if (!props.supplier) {
    return null
  }
  return get_supplier(props.supplier)
})
const showErrorMask = (msg?: string) => {
  if (!msg) {
    return
  }
  errorMsg.value = msg
  setTimeout(() => {
    errorMsg.value = ''
  }, 3000)
}
const emits = defineEmits(['edit', 'del', 'default'])

const handleDefault = () => {
  emits('default')
}

const handleDel = () => {
  emits('del', { id: props.id, name: props.name, default_model: props.isDefault })
}

const handleEdit = () => {
  emits('edit')
}

defineExpose({ showErrorMask })
</script>

<template>
  <div
    v-loading="!!errorMsg"
    class="model-card"
    :class="{ 'is-default': isDefault }"
    :element-loading-text="errorMsg"
    element-loading-custom-class="model-card-loading"
  >
    <div class="name-icon">
      <img
        v-if="current_supplier"
        :src="current_supplier.icon"
        :alt="current_supplier.name"
        width="32"
        height="32"
      />
      <span :title="name" class="name">{{ name }}</span>
      <span v-if="isDefault" class="default">{{ $t('model.default_model') }}</span>
    </div>
    <div class="type-value">
      <span class="type">{{ $t('model.model_type') }}</span>
      <span class="value">
        {{ modelType.startsWith('modelType.') ? $t(modelType) : modelType }}</span
      >
    </div>
    <div class="type-value base-model">
      <span class="type">{{ $t('model.basic_model') }}</span>
      <span class="value" :title="baseModel">{{ baseModel }}</span>
    </div>
    <div class="methods">
      <el-tooltip
        v-if="isDefault"
        effect="dark"
        :content="$t('common.the_default_model')"
        placement="top"
      >
        <el-button secondary disabled>
          <el-icon style="margin-right: 4px" size="16">
            <icon_admin_outlined></icon_admin_outlined>
          </el-icon>
          {{ $t('common.as_default_model') }}
        </el-button>
      </el-tooltip>

      <el-button v-else secondary @click="handleDefault">
        <el-icon style="margin-right: 4px" size="16">
          <icon_admin_outlined></icon_admin_outlined>
        </el-icon>
        {{ $t('common.as_default_model') }}
      </el-button>
      <el-button type="primary" plain @click="handleEdit">
        <el-icon style="margin-right: 4px" size="16">
          <edit></edit>
        </el-icon>
        {{ $t('dashboard.edit') }}
      </el-button>
      <el-button class="delete-action" text @click="handleDel">
        <el-icon style="margin-right: 4px" size="16">
          <delIcon></delIcon>
        </el-icon>
        {{ $t('dashboard.delete') }}
      </el-button>
    </div>
  </div>
</template>

<style lang="less" scoped>
.model-card {
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #e0e6ef;
  padding: 20px;
  border-radius: 12px;
  background: #fff;
  &.is-default {
    border-color: #abc8f5;
  }
  &:hover,
  &:focus-within {
    border-color: #8cb5ef;
    box-shadow: 0 4px 14px #20385a0a;
  }

  .name-icon {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin-bottom: 18px;
    img {
      flex-shrink: 0;
    }
    .name {
      flex: 1;
      min-width: 0;
      overflow-wrap: anywhere;
      font-weight: 600;
      font-size: 16px;
      line-height: 24px;
    }
    .default {
      margin-left: auto;
      flex-shrink: 0;
      background: #edf4ff;
      padding: 2px 8px;
      border-radius: 6px;
      color: #2463d6;
      font-weight: 400;
      font-size: 12px;
      line-height: 20px;
    }
  }

  .type-value {
    margin-top: 8px;
    display: flex;
    align-items: flex-start;
    font-weight: 400;
    font-size: 14px;
    line-height: 22px;
    .type {
      flex-shrink: 0;
      color: #646a73;
    }

    .value {
      margin-left: 16px;
      min-width: 0;
      overflow-wrap: anywhere;
    }
  }

  .base-model {
    display: block;
    margin-top: 16px;
    padding: 12px;
    border-radius: 8px;
    background: #f6f8fb;
    .type {
      display: block;
      font-size: 12px;
      margin-bottom: 4px;
    }
    .value {
      display: block;
      margin: 0;
      font-size: 13px;
      line-height: 20px;
    }
  }

  .methods {
    margin-top: auto;
    padding-top: 20px;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
    display: flex;
    :deep(.ed-button + .ed-button) {
      margin-left: 0;
    }
    .delete-action {
      margin-left: auto !important;
      color: #646a73;
    }
    .delete-action:hover {
      color: var(--ed-color-danger);
    }
  }
  :deep(.model-card-loading) {
    border-radius: 12px;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    align-items: end;
    background-color: rgb(122 122 122 / 87%);
    .ed-loading-spinner {
      top: auto;
      margin: 8px 4px;
      display: flex;
      position: relative;
      justify-content: flex-end;
      align-items: center;
      width: calc(100% - 8px);
    }
    svg {
      display: none;
    }
    p {
      text-align: left;
      color: var(--ed-color-danger);
    }
  }
}
</style>
