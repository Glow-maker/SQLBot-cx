<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { stageLabels, routeDetailLabels, stepElapsed, progressWarning, type ChatProgress } from './chatProgress'
const props = defineProps<{ progress: ChatProgress }>()
const emit = defineEmits<{ toggle: []; stop: [] }>()
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
watch(() => props.progress.outcome, outcome => {
  clearInterval(timer)
  now.value = Date.now()
  if (outcome === 'running') timer = setInterval(() => { now.value = Date.now() }, 1000)
}, { immediate: true })
onBeforeUnmount(() => clearInterval(timer))
const total = computed(() => Math.max(0, Math.floor(((props.progress.endedAt ?? now.value) - props.progress.startedAt) / 1000)))
const warning = computed(() => progressWarning(props.progress, now.value))
const details: Record<string, string> = {
  preparing_context: '正在准备查询上下文', waiting_model: '正在等待模型返回查询语句',
  receiving_model: '正在生成查询语句', reuse_datasource: '复用当前数据源，无需重新选表',
  execution_not_requested: '本次不执行查询，未进行执行前复核', not_requested: '本次无需执行',
}
</script>

<template>
  <section class="chat-progress" :class="progress.outcome" aria-label="处理进度">
    <div class="progress-heading">
      <span class="progress-title" role="status" aria-live="polite">{{ progress.message }}</span>
      <span class="total-time">已用时 {{ total }} 秒</span>
      <button v-if="progress.structured" class="toggle" :aria-expanded="progress.expanded" @click="emit('toggle')">
        {{ progress.expanded ? '收起进度' : '查看进度' }}
      </button>
    </div>
    <ol v-if="progress.structured && progress.expanded" class="steps">
      <li v-for="step in progress.steps" :key="step.stage" :class="step.status">
        <span class="step-mark" aria-hidden="true">{{ { completed: '✓', running: '●', failed: '!', skipped: '−', pending: '○' }[step.status] }}</span>
        <div class="step-copy">
          <span>{{ stageLabels[step.stage] }}</span>
          <small v-if="details[step.detail] && (step.status === 'running' || step.status === 'skipped')">{{ details[step.detail] }}</small>
          <ol v-if="step.details.length" class="route-details" aria-label="查找数据表明细">
            <li v-for="child in step.details" :key="child.stage" :class="child.status">
              <span>{{ routeDetailLabels[child.stage] }}</span>
              <span class="step-time">{{ child.status === 'skipped' ? '已跳过' : `${(stepElapsed(child, progress.endedAt ?? now) / 1000).toFixed(1)} 秒` }}{{ child.status === 'failed' ? ' · 失败' : child.status === 'running' ? ' · 进行中' : child.status === 'pending' ? ' · 未确认' : '' }}</span>
            </li>
          </ol>
        </div>
        <span class="step-time">
          {{ step.status === 'pending' ? (progress.outcome === 'running' ? '待处理' : progress.outcome === 'stopped' ? '未确认' : '未执行') : step.status === 'skipped' ? '已跳过' : `${(stepElapsed(step, progress.endedAt ?? now) / 1000).toFixed(1)} 秒` }}
          <span class="sr-only">{{ step.status === 'completed' ? '已完成' : step.status === 'failed' ? '失败' : step.status === 'running' ? '进行中' : '' }}</span>
        </span>
      </li>
    </ol>
    <p v-if="warning" class="wait-hint">{{ warning }}</p>
    <button v-if="progress.outcome === 'running'" class="stop" @click="emit('stop')">停止接收</button>
  </section>
</template>

<style scoped lang="less">
.chat-progress { margin: 0 0 14px; padding: 12px 16px; max-width: 720px; border: 1px solid var(--ed-color-border, #e4e7ed); border-radius: 12px; background: var(--ed-color-bg, #f8fafc); font-size: 13px; line-height: 1.6; }
.progress-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; }
.progress-title { font-weight: 600; color: var(--ed-color-primary, #409eff); }
.total-time, .step-time { color: #646a73; font-variant-numeric: tabular-nums; }
.toggle { margin-left: auto; border: 0; background: transparent; color: var(--ed-color-primary, #409eff); cursor: pointer; font: inherit; padding: 2px; }
.toggle:focus-visible { outline: 2px solid #409eff; outline-offset: 3px; }
.steps { list-style: none; margin: 12px 0 0; padding: 0; }
li { display: flex; gap: 10px; padding: 5px 0; align-items: baseline; color: #646a73; }
.step-mark { width: 16px; flex: none; text-align: center; }
.step-copy { flex: 1; min-width: 0; }
.step-copy small { display: block; font-size: 12px; color: #646a73; font-weight: normal; }
.route-details { list-style: none; margin: 7px 0 3px; padding: 0 0 0 10px; border-left: 2px solid #e4e7ed; }
.route-details li { justify-content: space-between; font-size: 12px; font-weight: normal; padding: 3px 0; }
.route-details .failed { color: #c45656; }
.step-time { flex: none; font-size: 12px; }
.steps li.running { color: var(--ed-color-primary, #409eff); font-weight: 600; }
.completed .step-mark { color: #31815b; }
.steps li.failed .step-mark, .chat-progress.failed .progress-title { color: #c45656; }
.pending, .skipped { color: #8a9099; }
.wait-hint { color: #916619; margin: 10px 0 0; font-size: 12px; }
.stop { border: 0; background: transparent; padding: 0; margin-top: 10px; font: inherit; color: #646a73; cursor: pointer; }
.stop:focus-visible { outline: 2px solid #409eff; outline-offset: 3px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
@media (max-width: 480px) { .chat-progress { padding: 12px; } .progress-title { flex-basis: 100%; } }
</style>
