<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import AssistantAvatar from './AssistantAvatar.vue'
import type { AssistantState, AssistantVariant } from './model'
import type { IdleMotion } from './idleMotion'
const props = withDefaults(
  defineProps<{
    variant: AssistantVariant
    animated: boolean
    idleMotion: IdleMotion
    state?: AssistantState
    engaged?: boolean
    motionReplay?: number
  }>(),
  { state: 'idle', engaged: false, motionReplay: 0 }
)
const reaction = ref(false)
const replay = ref(0)
let timer: ReturnType<typeof setTimeout> | undefined
function greet() {
  if (reaction.value || !props.animated || props.state !== 'idle' || props.engaged) return
  reaction.value = true
  replay.value++
  timer = setTimeout(() => {
    reaction.value = false
  }, 2400)
}
function cancel() {
  clearTimeout(timer)
  reaction.value = false
}
watch(
  () => [
    props.state,
    props.animated,
    props.engaged,
    props.variant,
    props.idleMotion,
    props.motionReplay,
  ],
  cancel
)
onBeforeUnmount(cancel)
</script>

<template>
  <button
    class="assistant-companion"
    type="button"
    aria-label="和助手打个招呼"
    :title="
      !animated
        ? '动态效果已关闭'
        : state === 'idle' && !engaged
          ? '点一下，和我打个招呼'
          : '正在专注当前问题'
    "
    :disabled="!animated || state !== 'idle' || engaged"
    @click="greet"
  >
    <AssistantAvatar
      :variant="variant"
      :animated="animated"
      :state="state"
      :size="112"
      :idle-motion="reaction ? 'bounce' : idleMotion"
      :motion-replay="motionReplay + replay"
      :engaged="engaged"
      follow-pointer
      grounded
    />
  </button>
</template>

<style scoped>
.assistant-companion {
  display: inline-grid;
  place-items: center;
  width: 144px;
  height: 144px;
  flex: none;
  border: 0;
  border-radius: 50%;
  padding: 16px;
  background: transparent;
  cursor: pointer;
  color: inherit;
}
.assistant-companion:hover:not(:disabled) {
  background: var(--assistant-tint, #edf3ff);
}
.assistant-companion:disabled {
  cursor: default;
}
@media (max-width: 640px) {
  .assistant-companion {
    width: 120px;
    height: 120px;
  }
  .assistant-companion :deep(.assistant-avatar) {
    width: 88px !important;
    height: 88px !important;
  }
}
</style>
