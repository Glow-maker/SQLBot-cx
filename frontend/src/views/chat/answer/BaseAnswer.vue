<script setup lang="ts">
import { type ChatMessage } from '@/api/chat.ts'
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import MdComponent from '@/views/chat/component/MdComponent.vue'
import icon_up_outlined from '@/assets/svg/icon_up_outlined.svg'
import icon_down_outlined from '@/assets/svg/icon_down_outlined.svg'
import { useI18n } from 'vue-i18n'
import { useChatConfigStore } from '@/stores/chatConfig.ts'

const props = withDefaults(
  defineProps<{
    message: ChatMessage
    loading?: boolean
    progressLabel?: string
    showWaiting?: boolean
    reasoningName:
      | 'sql_answer'
      | 'chart_answer'
      | 'analysis_thinking'
      | 'predict'
      | Array<'sql_answer' | 'chart_answer' | 'analysis_thinking' | 'predict'>
  }>(),
  {
    loading: false,
    progressLabel: '正在处理问题',
    showWaiting: true,
  }
)

const { t } = useI18n()

const chatConfig = useChatConfigStore()

const show = ref<boolean>(false)
const elapsedSeconds = ref(0)
let waitTimer: ReturnType<typeof setInterval> | undefined
watch(() => props.message.isTyping && props.showWaiting, (active) => {
  clearInterval(waitTimer)
  waitTimer = undefined
  elapsedSeconds.value = 0
  if (active) {
    const started = Date.now()
    waitTimer = setInterval(() => {
      elapsedSeconds.value = Math.floor((Date.now() - started) / 1000)
    }, 1000)
  }
}, { immediate: true })
onBeforeUnmount(() => clearInterval(waitTimer))

const reasoningContent = computed<Array<string>>(() => {
  const names: Array<'sql_answer' | 'chart_answer' | 'analysis_thinking' | 'predict'> = []
  if (typeof props.reasoningName === 'string') {
    names.push(props.reasoningName)
  } else {
    props.reasoningName.forEach((item) => {
      names.push(item)
    })
  }
  const result: Array<string> = []
  names.forEach((item) => {
    if (props.message?.record) {
      if (props.message?.record[item]) {
        result.push(props.message?.record[item] ?? '')
      }
    }
  })
  return result
})

// VITE_SHOW_REASONING=false 时隐藏"查看思考过程"按钮（中台嵌入场景隐去 LLM 推理）
const showReasoning = (import.meta.env.VITE_SHOW_REASONING ?? 'true') !== 'false'

const hasReasoning = computed<boolean>(() => {
  if (!showReasoning) return false
  if (reasoningContent.value.length > 0) {
    for (let i = 0; i < reasoningContent.value.length; i++) {
      if (reasoningContent.value[i] && reasoningContent.value[i].trim() !== '') {
        return true
      }
    }
  }
  return false
})

function clickShow() {
  show.value = !show.value
}

onMounted(() => {
  if (props.message.isTyping) {
    // 根据配置项是否默认展开
    show.value = chatConfig.getExpandThinkingBlock
  }
})
</script>

<template>
  <div class="base-answer-block">
    <slot name="progress"></slot>
    <el-button v-if="showReasoning && (message.isTyping || hasReasoning)" class="thinking-btn" @click="clickShow">
      <div class="thinking-btn-inner">
        <span v-if="message.isTyping">{{ t('qa.thinking') }}</span>
        <span v-else>{{ t('qa.thinking_step') }}</span>
        <span class="btn-icon">
          <el-icon v-if="show">
            <icon_up_outlined />
          </el-icon>
          <el-icon v-else>
            <icon_down_outlined />
          </el-icon>
        </span>
      </div>
    </el-button>
    <div v-if="hasReasoning && show" class="reasoning-content">
      <div v-for="(reason, _index) in reasoningContent" :key="_index" class="reasoning">
        <MdComponent :message="reason" />
      </div>
    </div>
    <div class="answer-container">
      <slot></slot>
      <div v-if="message.isTyping && showWaiting" class="answer-progress" role="status" aria-live="polite">
        <span class="progress-spinner" aria-hidden="true"></span>
        <span>{{ progressLabel }}</span>
        <span class="progress-time" aria-hidden="true">已等待 {{ elapsedSeconds }} 秒</span>
      </div>
      <slot name="tool"></slot>
      <slot name="footer"></slot>
    </div>
  </div>
</template>

<style scoped lang="less">
.base-answer-block {
  .answer-progress {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 10px;
    min-height: 28px;
    font-size: 13px;
    color: var(--ed-color-primary, #409eff);
  }
  .progress-time { color: #646a73; font-variant-numeric: tabular-nums; }
  .progress-spinner {
    width: 16px;
    height: 16px;
    flex: none;
    border: 2px solid rgba(64, 158, 255, 0.2);
    border-top-color: currentColor;
    border-radius: 50%;
    animation: answer-progress-spin 0.75s linear infinite;
  }
  .thinking-btn {
    height: 32px;
    padding: 5px 12px;

    --ed-button-text-color: rgba(31, 35, 41, 1);
    --ed-button-hover-text-color: var(--ed-button-text-color);
    --ed-button-active-text-color: var(--ed-button-text-color);
    --ed-button-bg-color: rgba(255, 255, 255, 1);
    --ed-button-hover-bg-color: rgba(245, 246, 247, 1);
    --ed-button-active-bg-color: rgba(239, 240, 241, 1);
    --ed-button-border-color: rgba(217, 220, 223, 1);
    --ed-button-hover-border-color: var(--ed-button-border-color);
    --ed-button-active-border-color: var(--ed-button-border-color);

    --ed-button-font-weight: 400;

    .thinking-btn-inner {
      display: flex;
      flex-direction: row;
      align-items: center;

      line-height: 22px;
      font-weight: 400;
      font-size: 14px;
    }
    .btn-icon {
      margin-left: 4px;
    }
  }

  .reasoning-content {
    margin-top: 8px;
    display: flex;
    flex-direction: column;
    padding-left: 9px;
    border-left: 1px solid rgba(31, 35, 41, 0.15);
    gap: 8px;

    .reasoning {
      width: 100%;
      line-height: 22px;
      font-weight: 400;
      font-size: 14px;
      color: rgba(143, 149, 158, 1) !important;

      .markdown-body {
        color: rgba(143, 149, 158, 1) !important;
        line-height: 22px;
        font-weight: 400;
        font-size: 14px;
      }

      padding-bottom: 8px;
      border-bottom: 1px solid rgba(31, 35, 41, 0.15);

      &:last-child {
        padding-bottom: unset;
        border-bottom: unset;
      }
    }
  }

  .answer-container {
    width: 100%;

    line-height: 24px;
    font-size: 16px;
    font-weight: 400;
    color: rgba(31, 35, 41, 1);
  }
}
@keyframes answer-progress-spin { to { transform: rotate(360deg); } }
</style>
