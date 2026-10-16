const assert = require('node:assert/strict')
const { test } = require('node:test')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { transformSync } = require('esbuild')
const { createPinia, defineStore } = require('pinia')
const { webcrypto } = require('node:crypto')

function loadModule(name, modules = {}) {
  const source = fs.readFileSync(path.join(__dirname, '../src', name), 'utf8')
  const code = transformSync(source, { loader: 'ts', format: 'cjs' }).code
  const sandbox = {
    module: { exports: {} },
    URLSearchParams,
    crypto: webcrypto,
    Uint8Array,
    Date,
    require: (key) => {
      assert.ok(key in modules, `Unexpected module ${key}`)
      return modules[key]
    },
  }
  vm.runInNewContext(code, sandbox)
  return sandbox.module.exports
}
const policy = loadModule('views/system/operations/operationsPolicy.ts')
const chatProgress = loadModule('views/chat/answer/chatProgress.ts')

test('operations authorization does not grant model or user administration', () => {
  const operator = { instance_admin: false, operator: true }
  assert.equal(policy.canOpenSystemRoute({ operations: true }, false, operator), true)
  assert.equal(policy.canOpenSystemRoute({}, false, operator), false)
  assert.equal(
    policy.canOpenSystemRoute({ operations: true, instanceAdmin: true }, false, operator),
    false
  )
  assert.equal(
    policy.canOpenSystemRoute({ operations: true }, true, {
      operator: false,
      instance_admin: false,
    }),
    false
  )
  assert.equal(policy.canOpenSystemRoute({}, true, operator), true)
})

test('missing, string, or revoked permissions fail closed', () => {
  for (const access of [
    {},
    { operator: 'true' },
    { instance_admin: 'true' },
    { operator: false, instance_admin: false },
  ]) {
    assert.equal(policy.canOpenOperations(access), false)
  }
})

test('date filtering includes the full selected end day', () => {
  const result = policy.dateQuery(['2026-10-15', '2026-10-16'])
  assert.equal(result.date_from, new Date('2026-10-15T00:00:00').toISOString())
  assert.equal(result.date_to, new Date('2026-10-17T00:00:00').toISOString())
  assert.deepEqual(Object.keys(policy.dateQuery(null)), [])
})

test('zero duration is measured rather than missing, and absent duration stays unknown', () => {
  assert.equal(policy.displayDuration(0), '0.00 秒')
  assert.equal(policy.displayDuration(1267), '1.27 秒')
  assert.equal(policy.displayDuration(null), '—')
  assert.equal(policy.displayDuration('not-a-number'), '—')
})

test('query result labels distinguish execution from correctness and do not infer old outcomes', () => {
  assert.equal(policy.statusLabel('success'), '执行成功')
  assert.equal(policy.queryResultLabel('empty_result'), '返回 0 行')
  assert.equal(policy.queryResultLabel('all_null_result'), '返回行全为空值')
  assert.equal(policy.queryResultLabel('rows_returned'), '有结果')
  for (const outcome of [undefined, null, 'completed', 'unknown_error', 0]) {
    assert.equal(policy.queryResultLabel(outcome), '未记录')
  }
})

test('feedback idempotency keys are unique valid UUIDs', () => {
  const first = policy.makeSubmissionId()
  const second = policy.makeSubmissionId()
  assert.match(first, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)
  assert.notEqual(first, second)
})

test('operator context is locked to the server tenant and denial clears access', async () => {
  let response = { instance_admin: false, operator: true, tenant_id: 124, auth_source: 'platform' }
  const api = { access: async () => response, tenants: async () => [] }
  const module = loadModule('stores/operations.ts', {
    pinia: { defineStore },
    './index': { store: createPinia() },
    '@/api/operations': { operationsApi: api },
    '@/views/system/operations/operationsPolicy': policy,
  })
  const store = module.useOperationsStore()
  store.scope = 'local'
  store.tenantId = '999'
  await store.loadAccess()
  assert.equal(store.allowed, true)
  assert.equal(store.scope, 'platform')
  assert.equal(store.tenantId, '124')
  response = { ...response, operator: false }
  await store.loadAccess()
  assert.equal(store.allowed, false)
  api.access = async () => {
    throw new Error('session expired')
  }
  await store.loadAccess()
  assert.equal(store.allowed, false)
})

test('trusted platform admin defaults to the current tenant and preserves explicit scope selection', async () => {
  const module = loadModule('stores/operations.ts', {
    pinia: { defineStore },
    './index': { store: createPinia() },
    '@/api/operations': { operationsApi: {
      access: async () => ({ instance_admin: true, operator: true, tenant_id: 124, auth_source: 'platform' }),
    } },
    '@/views/system/operations/operationsPolicy': policy,
  })
  const store = module.useOperationsStore()
  await store.loadAccess()
  assert.equal(store.scope, 'platform')
  assert.equal(store.tenantId, '124')
  store.tenantId = '125'
  await store.loadAccess()
  assert.equal(store.tenantId, '125')
  assert.equal(policy.canOpenSystemRoute({ instanceAdmin: true }, true, store.access), true)
  assert.equal(policy.canOpenSystemRoute({ instanceAdmin: true }, false, { operator: true }), false)
})

test('knowledge requests carry the selected scope and cannot override it through filters', async () => {
  const calls = []
  const request = {
    get: async (...args) => calls.push(args),
    put: async (...args) => calls.push(args),
    delete: async (...args) => calls.push(args),
  }
  const store = {
    scope: 'platform',
    tenantId: '124',
    context: { scope: 'platform', tenant_id: '124' },
  }
  const module = loadModule('api/knowledge.ts', {
    '@/utils/request': { request },
    '@/stores/operations': { useOperationsStore: () => store },
    './operations': { operationsApi: {} },
  })
  const api = module.knowledgeApi('terminology')
  await api.getList(1, 20, '?word=A%26B&tenant_id=999&scope=local&dslist=8&dslist=9')
  assert.equal(calls[0][1].params.word, 'A&B')
  assert.equal(calls[0][1].params.tenant_id, '124')
  assert.equal(calls[0][1].params.scope, 'platform')
  assert.equal(JSON.stringify(calls[0][1].params.dslist), '["8","9"]')
  assert.equal(calls[0][1].paramsSerializer.indexes, null)
  await api.enable(8, 'false')
  assert.equal(calls[1][1].enabled, false)
  assert.equal(calls[1][2].params.tenant_id, '124')
  store.tenantId = ''
  assert.equal(module.knowledgeScopeReady(), false)
  await assert.rejects(api.updateEmbedded({ word: '示例' }), /请选择/)
  await assert.rejects(api.deleteEmbedded([8]), /请选择/)
  await assert.rejects(api.enable(8, true), /请选择/)
})
const vue = require('vue')
const { parse, compileScript } = require('@vue/compiler-sfc')
async function setupComponent(name, modules, props = {}, live = false) {
  const source = fs.readFileSync(path.join(__dirname, '../src', name), 'utf8')
  const { descriptor } = parse(source)
  const compiled = compileScript(descriptor, { id: 'operations-behavior-test' })
  const code = transformSync(compiled.content, { loader: 'ts', format: 'cjs' }).code
  const sandbox = {
    module: { exports: {} },
    Date,
    console,
    ElMessage: { warning() {}, success() {} },
    require: (key) => {
      if (key === 'vue') return vue
      assert.ok(key in modules, `Unexpected module ${key}`)
      return modules[key]
    },
  }
  vm.runInNewContext(code, sandbox)
  let state
  const component = sandbox.module.exports.default
  if (live) {
    const scope = vue.effectScope()
    state = scope.run(() => component.setup(props, { expose() {} }))
    state.stopTestScope = () => scope.stop()
    return state
  }
  const app = vue.createSSRApp({
    setup() {
      state = component.setup(props, { expose() {} })
      return () => null
    },
  })
  await require('vue/server-renderer').renderToString(app)
  return state
}

test('a report draft keeps provenance and stays disabled before human review', async () => {
  const store = {
    context: { scope: 'platform', tenant_id: '124' },
    scope: 'platform',
    tenantId: '124',
  }
  const page = await setupComponent(
    'views/system/operations/OperationsPage.vue',
    {
      '@/views/chat/answer/chatProgress': chatProgress,
      'vue-router': { useRoute: () => ({ query: {} }), useRouter: () => ({ push() {} }) },
      '@/api/operations': { operationsApi: {} },
      '@/stores/operations': { useOperationsStore: () => store },
      './OperationsContext.vue': { default: {} },
      './operationsPolicy': policy,
    },
    { section: 'reports' }
  )
  page.detail.value = { id: 32, tenant_id: 124, scope: 'platform' }
  page.createDraft('data-training', {
    draft_id: 'draft-1',
    title: '总金额',
    content: 'SELECT SUM(amount) FROM orders',
  })
  assert.equal(store.draft.values.report_id, 32)
  assert.equal(store.draft.values.draft_id, 'draft-1')
  assert.equal(store.draft.values.enabled, false)
  assert.equal(store.draft.values.description, 'SELECT SUM(amount) FROM orders')
  assert.equal(store.draft.tenantId, '124')
})

test('interaction detail displays backend-captured progress, substage, and model elapsed times', async () => {
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/operations-stages.json'), 'utf8'))
  const page = await setupComponent('views/system/operations/OperationsPage.vue', {
    '@/views/chat/answer/chatProgress': chatProgress,
    'vue-router': { useRoute: () => ({ query: {} }), useRouter: () => ({ push() {} }) },
    '@/api/operations': { operationsApi: {} },
    '@/stores/operations': { useOperationsStore: () => ({ context: {}, tenantId: '124' }) },
    './OperationsContext.vue': { default: {} },
    './operationsPolicy': policy,
  }, { section: 'interactions' })
  assert.equal(page.rowDuration(fixture), '27.02 秒')
  assert.deepEqual(Array.from(fixture.stages_json, row => page.rowDuration(row)), [
    '1.25 秒', '0.24 秒', '0.00 秒', '15.00 秒', '0.03 秒', '0.34 秒', '9.00 秒',
    '1.13 秒', '14.32 秒', '8.52 秒',
  ])
  assert.equal(page.rowDuration({ stage: 'query' }), '—')
  assert.equal(page.rowDuration({ stage: 'route', detailStage: 'metadata', elapsedMs: 800 }), '—')
  assert.equal(page.stageLabel(fixture.stages_json[0]), '查找相关数据表')
  assert.equal(page.stageLabel(fixture.stages_json[1]), '查找相关数据表 / 加载可查询目录')
  assert.equal(page.stageLabel(fixture.stages_json[7]), 'CHOOSE_TABLE')
})

test('overview displays measured empty-result counts and sends and clears the independent result filter', async () => {
  const calls = []
  const summary = { success: 4, empty_results: 1, all_null_results: 2, outcome_counts: {
    empty_result: 1, all_null_result: 2, rows_returned: 1,
  } }
  const page = await setupComponent('views/system/operations/OperationsPage.vue', {
    '@/views/chat/answer/chatProgress': chatProgress,
    'vue-router': { useRoute: () => ({ query: {} }), useRouter: () => ({ push() {} }) },
    '@/api/operations': { operationsApi: { overview: async (params) => { calls.push(params); return summary } } },
    '@/stores/operations': { useOperationsStore: () => ({ context: { scope: 'platform', tenant_id: '124' }, tenantId: '124' }) },
    './OperationsContext.vue': { default: {} },
    './operationsPolicy': policy,
  }, { section: 'overview' })
  page.filters.outcome = 'empty_result'
  await page.load()
  assert.equal(calls[0].outcome, 'empty_result')
  assert.equal(calls[0].status, undefined)
  assert.equal(page.overviewCards.value.find(card => card.label === '返回 0 行').value, 1)
  assert.equal(page.overviewCards.value.find(card => card.label === '返回行全为空值').value, 2)
  page.reset()
  assert.equal(page.filters.outcome, '')
  assert.equal(page.query.value.outcome, undefined)
})

test('feedback double-click makes one request and success receives a fresh idempotency key', async () => {
  const requests = []
  let finish
  const store = vue.reactive({
    feedbackOpen: false,
    feedbackRecordId: 5,
    feedbackInteractionId: 18,
    feedbackTab: 'submit',
  })
  const component = await setupComponent('components/operations/FeedbackDialog.vue', {
    '@/api/operations': {
      operationsApi: {
        submitFeedback: (body) => {
          requests.push(body)
          return new Promise((resolve) => {
            finish = resolve
          })
        },
        myFeedback: async () => ({ items: [], total: 0 }),
      },
    },
    '@/stores/operations': { useOperationsStore: () => store },
    '@/views/system/operations/operationsPolicy': policy,
  })
  component.form.content = '执行结果错误'
  component.form.submission_id = policy.makeSubmissionId()
  const originalKey = component.form.submission_id
  const first = component.submit()
  await component.submit()
  assert.equal(requests.length, 1)
  assert.equal(requests[0].record_id, 5)
  assert.equal(requests[0].interaction_id, 18)
  finish()
  await first
  assert.equal(store.feedbackTab, 'mine')
  assert.notEqual(component.form.submission_id, originalKey)
  assert.equal(component.saving.value, false)
})

test('local workspace knowledge keeps its legacy API while operations uses scoped API', async () => {
  for (const [view, apiName, kind] of [
    ['professional', 'professionalApi', 'terminology'],
    ['training', 'trainingApi', 'data-training'],
  ]) {
    const localApi = { getList() {} }
    const scopedApi = { getList() {} }
    const route = vue.reactive({ path: `/set/${view}`, meta: {} })
    const store = { context: {}, tenantId: '', scope: 'platform', draft: null }
    const source = fs.readFileSync(
      path.join(__dirname, `../src/views/system/${view}/index.vue`),
      'utf8'
    )
    const imports = Array.from(source.matchAll(/from\s+['"]([^'"]+)['"]/g), (match) => match[1])
    const stubs = Object.fromEntries(
      imports
        .filter((name) => name.endsWith('.svg') || name.endsWith('.vue'))
        .map((name) => [name, { __esModule: true, default: {} }])
    )
    const component = await setupComponent(`views/system/${view}/index.vue`, {
      ...stubs,
      '@/api/knowledge': {
        knowledgeApi: (value) => {
          assert.equal(value, kind)
          return scopedApi
        },
        knowledgeScopeReady: () => false,
        knowledgeSources: async () => [],
      },
      '@/stores/operations': { useOperationsStore: () => store },
      'vue-router': { useRoute: () => route },
      '@/api/datasource': { datasourceApi: { list: async () => [] } },
      [`@/api/${view}`]: { [apiName]: localApi },
      '@/utils/date': { formatTimestamp() {} },
      'vue-i18n': { useI18n: () => ({ t: (value) => value }) },
      'lodash-es': { cloneDeep: (value) => structuredClone(value) },
      '@/components/filter-text': { convertFilterText() {}, FilterText: {} },
      '@/components/drawer-main': { DrawerMain: {} },
      '@vueuse/core': { useClipboard: () => ({ copy() {} }) },
      '@/stores/user': { useUserStore: () => ({ oid: '1' }) },
      '@/api/embedded.ts': { getAdvancedApplicationList: async () => [] },
    })
    assert.equal(component.operationsMode.value, false)
    assert.equal(component.api.value, localApi)
    assert.equal(component.pageForm.value.enabled, undefined)
    route.meta.operations = true
    assert.equal(component.operationsMode.value, true)
    assert.equal(component.api.value, scopedApi)
  }
})

test('feedback history rejects out-of-order responses after closing or changing answers', async () => {
  const pending = []
  const store = vue.reactive({ feedbackOpen: false, feedbackRecordId: 1, feedbackTab: 'mine' })
  const component = await setupComponent(
    'components/operations/FeedbackDialog.vue',
    {
      '@/api/operations': {
        operationsApi: {
          myFeedback: (params) => new Promise((resolve) => pending.push({ params, resolve })),
        },
      },
      '@/stores/operations': { useOperationsStore: () => store },
      '@/views/system/operations/operationsPolicy': policy,
    },
    {},
    true
  )
  const settle = async () => {
    await vue.nextTick()
    await vue.nextTick()
  }
  try {
    store.feedbackOpen = true
    await settle()
    store.feedbackOpen = false
    await settle()
    store.feedbackRecordId = 2
    store.feedbackOpen = true
    await settle()
    assert.equal(pending[0].params.record_id, 1)
    assert.equal(pending[1].params.record_id, 2)
    pending[0].resolve({ items: [{ id: 11 }], total: 1 })
    await settle()
    assert.equal(component.history.value.length, 0)
    assert.equal(component.loading.value, true)
    pending[1].resolve({ items: [{ id: 22 }], total: 1 })
    await settle()
    assert.equal(component.history.value[0].id, 22)
    store.feedbackRecordId = 3
    await settle()
    store.feedbackRecordId = 4
    await settle()
    pending[3].resolve({ items: [{ id: 44 }], total: 1 })
    await settle()
    pending[2].resolve({ items: [{ id: 33 }], total: 8 })
    await settle()
    assert.equal(component.history.value[0].id, 44)
    assert.equal(component.total.value, 1)
    store.feedbackRecordId = 5
    await settle()
    store.feedbackOpen = false
    await settle()
    pending[4].resolve({ items: [{ id: 55 }], total: 1 })
    await settle()
    assert.equal(component.history.value.length, 0)
    assert.equal(component.total.value, 0)
    assert.equal(component.loading.value, false)
  } finally {
    component.stopTestScope()
  }
})
