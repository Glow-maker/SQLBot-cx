<script setup lang="ts">
import { ref } from 'vue'
import { onClickOutside } from '@vueuse/core'
import { Setting, ArrowDown } from '@element-plus/icons-vue'
import AssistantAvatar from './AssistantAvatar.vue'
import type { AssistantVariant } from './model'
import { idleMotionLabels, type IdleMotion } from './idleMotion'
const props = defineProps<{
  variant: AssistantVariant
  animated: boolean
  idleMotion: IdleMotion
  inline?: boolean
}>()
const emit = defineEmits<{
  'update:variant': [AssistantVariant]
  'update:animated': [boolean]
  'update:idleMotion': [IdleMotion]
}>()
const root = ref<HTMLDetailsElement>()
function close() {
  if (root.value && !props.inline) root.value.open = false
}
function escape() {
  close()
  if (!props.inline) root.value?.querySelector('summary')?.focus()
}
onClickOutside(root, close)
</script>

<template>
  <details
    ref="root"
    class="avatar-appearance"
    :class="{ 'is-inline': inline }"
    :open="inline || undefined"
    @keydown.esc="escape"
  >
    <summary>
      <Setting aria-hidden="true" /><span>助手外观</span
      ><ArrowDown class="chevron" aria-hidden="true" />
    </summary>
    <div class="appearance-options">
      <fieldset>
        <legend>选择形象</legend>
        <div class="variant-options">
          <button
            v-for="item in ['rocket', 'bloub'] as AssistantVariant[]"
            :key="item"
            type="button"
            :aria-pressed="variant === item"
            @click="emit('update:variant', item)"
          >
            <AssistantAvatar :variant="item" :size="40" :animated="false" />
            <span>{{ item === 'rocket' ? '原创火箭' : 'Bloub' }}</span>
          </button>
        </div>
      </fieldset>
      <label class="motion-switch">
        <span><strong>动态效果</strong><small>跟随指针与状态反馈</small></span>
        <input
          aria-label="动态效果"
          type="checkbox"
          role="switch"
          :checked="animated"
          @change="emit('update:animated', ($event.target as HTMLInputElement).checked)"
        />
      </label>
      <label class="motion-select">
        <span>待机动作</span>
        <select
          aria-label="待机动作"
          :value="idleMotion"
          :disabled="!animated"
          @change="
            emit('update:idleMotion', ($event.target as HTMLSelectElement).value as IdleMotion)
          "
        >
          <option v-for="(label, value) in idleMotionLabels" :key="value" :value="value">
            {{ label }}
          </option>
        </select>
      </label>
      <p class="settings-note">默认开启动效，不跟随系统减少动态效果设置。可在此关闭，仅保存在此浏览器。</p>
    </div>
  </details>
</template>

<style scoped>
.avatar-appearance {
  position: relative;
  font-size: 13px;
  line-height: 1.5;
  color: var(--assistant-ink, #202b40);
}
summary {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 9px 12px;
  border-radius: 10px;
  list-style: none;
  background: var(--assistant-surface, #fdfefe);
  border: 1px solid var(--assistant-border, #dde4ed);
  white-space: nowrap;
}
summary::-webkit-details-marker {
  display: none;
}
summary:hover {
  background: var(--assistant-soft, #f3f6fa);
}
summary svg {
  width: 16px;
  height: 16px;
}
summary .chevron {
  width: 12px;
  margin-left: 4px;
}
.appearance-options {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  z-index: 2;
  width: 264px;
  box-sizing: border-box;
  padding: 20px;
  border: 1px solid var(--assistant-border, #dde4ed);
  border-radius: 16px;
  background: var(--assistant-surface, #fdfefe);
  box-shadow: 0 12px 36px #20375b16;
}
.is-inline > summary {
  display: none;
}
.is-inline .appearance-options {
  position: static;
  width: 100%;
  padding: 0;
  border: 0;
  box-shadow: none;
  background: transparent;
}
fieldset {
  padding: 0;
  margin: 0 0 22px;
  border: 0;
  min-width: 0;
}
legend,
.motion-select > span {
  display: block;
  margin-bottom: 10px;
  font-weight: 600;
  font-size: 13px;
}
.variant-options {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.variant-options button {
  display: flex;
  align-items: center;
  flex-direction: column;
  gap: 8px;
  border: 1px solid var(--assistant-border, #dde4ed);
  border-radius: 12px;
  padding: 14px 8px;
  cursor: pointer;
  font: inherit;
  color: inherit;
  background: var(--assistant-surface, #fdfefe);
}
.variant-options button:hover {
  border-color: var(--assistant-accent, #3370ff);
}
.variant-options button[aria-pressed='true'] {
  border-color: var(--assistant-accent, #3370ff);
  background: var(--assistant-tint, #edf3ff);
  box-shadow: inset 0 0 0 1px var(--assistant-accent, #3370ff);
}
.motion-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 22px;
  cursor: pointer;
}
strong {
  font-weight: 600;
  font-size: 13px;
}
small {
  display: block;
  color: var(--assistant-muted, #58657a);
  font-size: 12px;
  margin-top: 3px;
}
input {
  appearance: none;
  flex: none;
  width: 36px;
  height: 22px;
  border-radius: 20px;
  margin: 0;
  padding: 3px;
  background: var(--assistant-muted, #58657a);
  cursor: pointer;
}
input::before {
  content: '';
  display: block;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fdfefe;
}
input:checked {
  background: var(--assistant-accent, #3370ff);
}
input:checked::before {
  transform: translateX(14px);
}
select {
  box-sizing: border-box;
  width: 100%;
  min-height: 40px;
  font: inherit;
  color: inherit;
  border: 1px solid var(--assistant-border, #dde4ed);
  border-radius: 8px;
  background: var(--assistant-surface, #fdfefe);
  padding: 8px 10px;
}
select:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.settings-note {
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--assistant-muted, #58657a);
  line-height: 1.7;
}
@media (prefers-reduced-motion: no-preference) {
  input::before {
    transition: transform 140ms ease;
  }
}
</style>
