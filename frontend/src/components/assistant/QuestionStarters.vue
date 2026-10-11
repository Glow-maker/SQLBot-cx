<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { datasourceApi } from '@/api/datasource'
import { catalogError, parseQuestionCatalog, type QuestionCatalog } from './questionCatalog'
const props = defineProps<{ platform?: boolean }>()
const emit = defineEmits<{ select: [string] }>()
const catalog = ref<QuestionCatalog>()
const loading = ref(false)
const error = ref('')
let request: AbortController | undefined
async function loadCatalog() {
  request?.abort()
  catalog.value = undefined
  error.value = ''
  if (!props.platform) { loading.value = false; return }
  const active = new AbortController()
  request = active
  loading.value = true
  try {
    const value = await datasourceApi.questionStarters(active.signal)
    if (!active.signal.aborted) catalog.value = parseQuestionCatalog(value)
  } catch (e) {
    if (!active.signal.aborted) error.value = catalogError(e)
  } finally {
    if (!active.signal.aborted) loading.value = false
  }
}
watch(() => props.platform, loadCatalog, { immediate: true })
onMounted(() => window.addEventListener('sqlbot:identity-change', loadCatalog))
onBeforeUnmount(() => {
  request?.abort()
  window.removeEventListener('sqlbot:identity-change', loadCatalog)
})
const starters = [
  { label: '看趋势', detail: '按时间观察变化', text: '最近六个月，[指标]按月的变化趋势是什么？' },
  { label: '做对比', detail: '比较不同维度', text: '对比本月各[维度]的[指标]。' },
  { label: '找排名', detail: '找到表现靠前的项目', text: '本月[指标]排名前十的[对象]有哪些？' },
]
</script>
<template>
  <div class="question-starters">
    <template v-if="platform">
      <div class="catalog-heading"><p>你可以查询的数据 <span v-if="catalog">{{ catalog.total }} 张授权表</span></p><button class="refresh" type="button" :disabled="loading" @click="loadCatalog">{{ loading ? '加载中' : '刷新目录' }}</button></div>
      <p v-if="loading" class="catalog-notice" role="status">正在读取你的可查询目录…</p>
      <p v-else-if="error" class="catalog-notice catalog-error" role="alert">{{ error }}</p>
      <p v-else-if="catalog?.total === 0" class="catalog-notice">当前没有可查询的数据表，请联系数据管理员授权并上架所需业务表。</p>
      <template v-else-if="catalog">
        <div class="catalog-options">
          <button v-for="(item, index) in catalog.tables" :key="`${item.name}-${index}`" class="catalog-option" type="button" :aria-label="`填入问题：${item.question}`" @click="emit('select', item.question)">
            <strong>{{ item.name }}</strong><span v-if="item.description">{{ item.description }}</span><small>{{ item.question }}</small>
          </button>
        </div>
        <small>点击填入问题后发送{{ catalog.total > catalog.tables.length ? ` · 当前展示 ${catalog.tables.length} 张表` : '' }}。关联查询可在问题中说明需要的业务对象。</small>
      </template>
    </template>
    <template v-else>
    <p>从一个问题开始 <span>问法模板，点击填入</span></p>
    <div class="starter-options">
      <button
        v-for="item in starters"
        :key="item.label"
        type="button"
        @click="emit('select', item.text)"
      >
        <strong>{{ item.label }}</strong
        ><span>{{ item.detail }}</span>
      </button>
    </div>
    <small>请替换方括号内容，并确认当前数据源包含相关数据。</small>
    </template>
  </div>
</template>
<style scoped>
.question-starters {
  width: 100%;
  text-align: left;
}
.catalog-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.catalog-options { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 210px), 1fr)); gap: 10px; max-height: 290px; overflow-y: auto; padding: 2px; }
.catalog-option { display: flex; flex-direction: column; align-items: flex-start; text-align: left; gap: 5px; min-width: 0; }
.catalog-option strong { white-space: normal; overflow-wrap: anywhere; }
.catalog-option span { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
.catalog-option small { margin-top: 3px; color: var(--assistant-accent, #3370ff); }
.catalog-notice { padding: 12px 14px; border: 1px solid var(--assistant-border, #dde4ed); border-radius: 10px; font-weight: 400; line-height: 1.7; }
.catalog-error { color: #a65d14; }
button.refresh { border: 0; padding: 2px 0; background: transparent; color: var(--assistant-accent, #3370ff); white-space: nowrap; font-size: 12px; }
button:focus-visible { outline: 2px solid var(--assistant-accent, #3370ff); outline-offset: 2px; }
button:disabled { cursor: wait; opacity: .6; }
p {
  margin: 0 0 12px;
  color: var(--assistant-ink, #202b40);
  font-size: 13px;
  font-weight: 600;
}
p span {
  margin-left: 10px;
  color: var(--assistant-muted, #58657a);
  font-weight: 400;
  font-size: 12px;
}
.starter-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
button {
  display: flex;
  align-items: center;
  gap: 10px;
  font: inherit;
  color: var(--assistant-ink, #202b40);
  background: var(--assistant-surface, #fdfefe);
  border: 1px solid var(--assistant-border, #dde4ed);
  border-radius: 10px;
  padding: 11px 14px;
  cursor: pointer;
}
button:hover {
  border-color: var(--assistant-accent, #3370ff);
  background: var(--assistant-tint, #edf3ff);
}
button:active {
  transform: translateY(1px);
}
strong {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}
button span {
  font-size: 12px;
  color: var(--assistant-muted, #58657a);
}
small {
  display: block;
  margin-top: 10px;
  color: var(--assistant-muted, #58657a);
  font-size: 12px;
}
@media (max-width: 640px) {
  button span {
    display: none;
  }
}
@media (prefers-reduced-motion: reduce) {
  button:active {
    transform: none;
  }
}
</style>
