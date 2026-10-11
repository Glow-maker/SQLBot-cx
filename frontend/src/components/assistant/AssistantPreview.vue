<script setup lang="ts">
import { computed, ref } from 'vue'
import { RefreshRight, ArrowRight } from '@element-plus/icons-vue'
import AssistantAvatar from './AssistantAvatar.vue'
import AssistantCompanion from './AssistantCompanion.vue'
import AvatarAppearance from './AvatarAppearance.vue'
import QuestionStarters from './QuestionStarters.vue'
import { useAvatarPreferences } from './useAvatarPreferences'
import { stateLabels, type AssistantState } from './model'
import './assistant-ui.css'
const { variant, animated, idleMotion } = useAvatarPreferences()
const motionReplay = ref(0)
const state = ref<AssistantState>('idle')
const shown = ref(true)
const draft = ref('')
const input = ref<HTMLTextAreaElement>()
const focused = ref(false)
const states = Object.keys(stateLabels) as AssistantState[]
const engaged = computed(() => focused.value && state.value === 'idle')
const descriptions: Record<AssistantState, string> = {
  idle: '移动鼠标看向你，点一下打个招呼。',
  thinking: '居中专注，等待可见的回答内容。',
  responding: '跟随回答节奏，保持轻微动态。',
  success: '短暂回应后停稳，不循环庆祝。',
  error: '停止动作，把注意力留给错误说明。',
  stopped: '处理已停止，静静等待下一次提问。',
}
function replay() {
  state.value = 'idle'
  focused.value = false
  motionReplay.value++
}
function fillQuestion(value: string) {
  draft.value = value
  input.value?.focus()
}
</script>

<template>
  <div class="assistant-lab assistant-surface">
    <div class="lab-shell" data-assistant-surface>
      <header class="lab-header">
        <div class="lab-brand">
          <AssistantAvatar :size="28" variant="rocket" :animated="false" /><strong>SQLBot</strong
          ><span class="brand-context">助手体验</span>
        </div>
        <a href="./chat/index">打开问答页 <ArrowRight aria-hidden="true" /></a>
      </header>
      <main>
        <div class="lab-intro">
          <h1>让问数多一点温度</h1>
          <p>选择你的搭档，在这里试试它的目光、动作和回应。</p>
        </div>
        <div class="lab-layout">
          <section class="experience" aria-label="助手交互预览">
            <div v-if="shown" class="companion-stage">
              <AssistantCompanion
                :variant="variant"
                :animated="animated"
                :idle-motion="idleMotion"
                :motion-replay="motionReplay"
                :state="state"
                :engaged="engaged"
              />
              <h2>
                {{
                  engaged
                    ? '我在听，慢慢说'
                    : state === 'idle'
                      ? '你好，今天想了解什么？'
                      : stateLabels[state]
                }}
              </h2>
              <p>
                {{
                  !animated
                    ? '动态效果已关闭，保留静态形象与当前表情。'
                    : engaged
                      ? '这里的输入仅用于体验，不会发送到后端。'
                      : descriptions[state]
                }}
              </p>
            </div>
            <div v-else class="companion-stage">
              <h2>角色已隐藏</h2>
              <p>动画已卸载，可在“尺寸与运行检查”中重新显示。</p>
            </div>
            <div class="try-composer">
              <label for="assistant-try-question">试着写一个问题</label>
              <textarea
                id="assistant-try-question"
                ref="input"
                v-model="draft"
                rows="2"
                placeholder="输入时，它会停下玩耍，专注看向输入区。"
                @focus="focused = true"
                @blur="focused = false"
              />
              <div class="composer-note">
                <span>交互预览，不发送请求</span
                ><button v-if="draft" type="button" @click="draft = ''">清空</button>
              </div>
            </div>
            <QuestionStarters @select="fillQuestion" />
            <div v-if="shown" class="answer-sample">
              <div class="sample-heading">
                <h3>回答中的样子</h3>
                <span>28 px，保持轻量</span>
              </div>
              <div class="sample-row">
                <AssistantAvatar :variant="variant" :size="28" :animated="false" />
                <p>历史回答<span>保持静态，方便专心阅读。</span></p>
              </div>
              <div class="sample-row">
                <AssistantAvatar
                  :variant="variant"
                  :size="28"
                  :state="state"
                  :animated="animated && state !== 'idle'"
                />
                <p>
                  当前回答<span>{{ stateLabels[state] }}，此处为状态试播。</span>
                </p>
              </div>
            </div>
          </section>
          <aside class="lab-controls" aria-label="外观与动效设置">
            <h2>外观与动效</h2>
            <AvatarAppearance
              v-model:variant="variant"
              v-model:animated="animated"
              v-model:idle-motion="idleMotion"
              inline
            />
            <button type="button" class="replay-button" :disabled="!animated" @click="replay">
              <RefreshRight aria-hidden="true" />试播待机动作
            </button>
            <section class="state-controls">
              <h3>业务状态</h3>
              <p>手动切换，仅预览表情与动作。</p>
              <div>
                <button
                  v-for="item in states"
                  :key="item"
                  :aria-pressed="state === item"
                  type="button"
                  @click="state = item"
                >
                  {{ stateLabels[item] }}
                </button>
              </div>
            </section>
            <details class="preview-details">
              <summary>尺寸与运行检查</summary>
              <div v-if="shown" class="sizes">
                <AssistantAvatar :variant="variant" :size="88" :animated="false" /><AssistantAvatar
                  :variant="variant"
                  :size="28"
                  :animated="false"
                /><span>88 / 28 px</span>
              </div>
              <p>欢迎形象为 112px，窄屏为 88px。自动待机每 9 秒触发一个短动作。</p>
              <button type="button" @click="shown = !shown">
                {{ shown ? '隐藏角色' : '显示角色' }}
              </button>
            </details>
          </aside>
        </div>
      </main>
      <footer>这是独立体验页，不连接业务数据。形象与动作偏好可用于同一浏览器中的问答页。</footer>
    </div>
  </div>
</template>

<style scoped>
.assistant-lab {
  min-height: 100dvh;
  background: var(--assistant-soft);
  font:
    14px/1.6 'Segoe UI',
    'PingFang SC',
    'Microsoft YaHei',
    sans-serif;
}
.lab-shell {
  max-width: 1200px;
  margin: auto;
  padding: 0 40px;
}
.lab-header {
  height: 76px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--assistant-border);
}
.lab-brand {
  display: flex;
  align-items: center;
  gap: 10px;
}
.lab-brand strong {
  font-size: 18px;
  letter-spacing: -0.4px;
}
.lab-brand > .brand-context {
  color: var(--assistant-muted);
  padding-left: 16px;
  margin-left: 6px;
  border-left: 1px solid var(--assistant-border);
}
.lab-header a {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--assistant-ink);
  text-decoration: none;
  font-size: 13px;
}
.lab-header a:hover {
  text-decoration: underline;
  text-underline-offset: 5px;
}
.lab-header svg,
.replay-button svg {
  width: 16px;
  height: 16px;
}
.lab-intro {
  padding: 30px 0 24px;
}
h1 {
  margin: 0 0 6px;
  font-size: 28px;
  font-weight: 650;
  letter-spacing: -0.8px;
  line-height: 1.4;
}
.lab-intro p {
  margin: 0;
  color: var(--assistant-muted);
}
.lab-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 288px;
  align-items: start;
  gap: 24px;
}
.experience {
  padding: 28px 32px;
  background: var(--assistant-surface);
  border: 1px solid var(--assistant-border);
  border-radius: 20px;
  min-width: 0;
}
.companion-stage {
  min-height: 236px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
}
.companion-stage h2 {
  margin: 12px 0 6px;
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.4px;
}
.companion-stage p {
  margin: 0;
  font-size: 13px;
  color: var(--assistant-muted);
}
.try-composer {
  margin: 24px 0;
  padding: 16px 18px 10px;
  border: 1px solid var(--assistant-border);
  background: var(--assistant-soft);
  border-radius: 14px;
}
.try-composer:focus-within {
  border-color: var(--assistant-accent);
  box-shadow: 0 0 0 3px var(--assistant-tint);
}
.try-composer label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 8px;
}
textarea {
  box-sizing: border-box;
  display: block;
  width: 100%;
  resize: vertical;
  min-height: 54px;
  max-height: 160px;
  border: 0;
  background: transparent;
  color: var(--assistant-ink);
  font: inherit;
  outline: none;
}
textarea::placeholder {
  color: var(--assistant-muted);
}
.composer-note {
  display: flex;
  justify-content: space-between;
  align-items: center;
  min-height: 26px;
  color: var(--assistant-muted);
  font-size: 12px;
}
button {
  font: inherit;
  color: inherit;
  cursor: pointer;
}
.composer-note button {
  border: 0;
  background: transparent;
  padding: 4px;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.lab-controls {
  padding: 24px;
  border: 1px solid var(--assistant-border);
  border-radius: 20px;
  background: var(--assistant-surface);
}
.lab-controls > h2 {
  margin: 0 0 22px;
  font-size: 16px;
  font-weight: 600;
}
.replay-button {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 40px;
  margin: 16px 0 24px;
  border: 1px solid var(--assistant-border);
  border-radius: 8px;
  background: var(--assistant-soft);
  font-size: 13px;
  white-space: nowrap;
}
.replay-button:hover:not(:disabled) {
  border-color: var(--assistant-accent);
}
.replay-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
h3 {
  font-size: 13px;
  font-weight: 600;
  margin: 0;
}
.state-controls {
  padding-top: 20px;
  border-top: 1px solid var(--assistant-border);
}
.state-controls p,
.preview-details p {
  font-size: 12px;
  color: var(--assistant-muted);
  margin: 6px 0 12px;
}
.state-controls > div {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.state-controls button {
  min-height: 36px;
  border: 1px solid var(--assistant-border);
  border-radius: 8px;
  background: transparent;
  font-size: 12px;
  white-space: nowrap;
}
.state-controls button[aria-pressed='true'] {
  background: var(--assistant-tint);
  border-color: var(--assistant-accent);
}
.preview-details {
  margin-top: 24px;
  font-size: 12px;
}
.preview-details summary {
  cursor: pointer;
  color: var(--assistant-muted);
}
.preview-details button {
  padding: 6px 10px;
  border: 1px solid var(--assistant-border);
  border-radius: 8px;
  background: transparent;
}
.sizes {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 12px;
}
.sizes span {
  color: var(--assistant-muted);
}
.answer-sample {
  margin-top: 26px;
  padding-top: 20px;
  border-top: 1px solid var(--assistant-border);
}
.sample-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.sample-heading > span {
  font-size: 12px;
  color: var(--assistant-muted);
}
.sample-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
}
.sample-row p {
  margin: 0;
  font-size: 13px;
}
.sample-row p span {
  color: var(--assistant-muted);
  font-size: 12px;
  margin-left: 12px;
}
footer {
  padding: 22px 0 28px;
  color: var(--assistant-muted);
  font-size: 12px;
}
@media (max-width: 850px) {
  .lab-shell {
    padding: 0 20px;
  }
  .lab-layout {
    grid-template-columns: minmax(0, 1fr) 260px;
    gap: 16px;
  }
  .experience {
    padding: 24px;
  }
  .lab-controls {
    padding: 20px;
  }
}
@media (max-width: 640px) {
  .lab-shell {
    padding: 0 16px;
  }
  .lab-header {
    height: 64px;
  }
  .lab-brand > .brand-context {
    display: none;
  }
  h1 {
    font-size: 24px;
  }
  .lab-layout {
    grid-template-columns: 1fr;
  }
  .experience,
  .lab-controls {
    padding: 20px;
    border-radius: 16px;
  }
  .companion-stage {
    min-height: 220px;
  }
  .companion-stage h2 {
    font-size: 20px;
  }
  .sample-row p span {
    display: block;
    margin-left: 0;
  }
  .try-composer {
    padding: 14px;
  }
}
</style>
