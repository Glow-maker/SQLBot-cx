const assert = require('node:assert/strict')
const { test } = require('node:test')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { transformSync } = require('esbuild')
const axios = require('axios')

function fixture(responses = []) {
  let state = { token: 'current', tenantId: 124 }
  let renewals = 0
  const calls = []
  const value = { enabled: false, time: '22:00', timezone: 'Asia/Shanghai', force: false }
  const window = {
    __SQLBOT_GET_LOGIN_STATE__: () => state,
    __INJECTED_LOGIN_STATE__: { token: 'stale', tenantId: 999 },
    __SQLBOT_REFRESH_LOGIN_STATE__: async () => { renewals++; state = { ...state, token: 'renewed' }; return state },
  }
  const nextResponse = async config => {
    calls.push(config)
    const response = responses.shift() || { status: 200, data: { code: 0, data: value } }
    return typeof response === 'function' ? response(() => { state = {} }) : response
  }
  const requestModules = {
    axios: { ...axios, create(config) {
      const client = axios.create(config)
      client.defaults.adapter = async config => {
        const result = await nextResponse(config)
        const response = { ...result, statusText: '', headers: {}, config }
        if (response.status >= 400) throw new axios.AxiosError('Request rejected', 'ERR_BAD_REQUEST', config, null, response)
        return response
      }
      return client
    } },
    '@/utils/useCache': { useCache: () => ({ wsCache: new Map([['user.token', 'stale-local']]) }) },
    './utils': { getLocale: () => 'zh-CN' },
    '@/stores/assistant': { useAssistantStore: () => ({}) },
    'vue-router': { useRouter: () => ({}) },
    'json-bigint': { parse: JSON.parse },
    'vite-plugin-qiankun/es/helper': { qiankunWindow: { __POWERED_BY_QIANKUN__: true } },
  }
  const requestCode = transformSync(fs.readFileSync(path.join(__dirname, '../src/utils/request.ts'), 'utf8'), {
    loader: 'ts', format: 'cjs', define: { 'import.meta.env.VITE_API_BASE_URL': '"/api/v1"' },
  }).code
  const requestSandbox = { window, console, setTimeout, module: { exports: {} }, require(name) {
    assert.ok(name in requestModules, name)
    return requestModules[name]
  } }
  vm.runInNewContext(requestCode, requestSandbox)
  const source = fs.readFileSync(path.join(__dirname, '../src/api/indexSync.ts'), 'utf8')
  const code = transformSync(source, { loader: 'ts', format: 'cjs' }).code
  const sandbox = { window, module: { exports: {} }, require(name) {
    if (name === '@/utils/request') return requestSandbox.module.exports
    assert.equal(name, 'axios')
    return { request: nextResponse }
  } }
  vm.runInNewContext(code, sandbox)
  return { api: sandbox.module.exports, calls, window, setState: s => { state = s }, renewals: () => renewals }
}

test('reads shared configuration with current host identity and does not trigger sync', async () => {
  const f = fixture(); await f.api.getIndexSyncControl()
  assert.equal(f.calls.length, 1)
  assert.equal(f.calls[0].method, 'GET')
  assert.equal(f.calls[0].url, '/admin-api/dmgt/data-source/index-sync')
  assert.equal(f.calls[0].headers.Authorization, 'Bearer current')
  assert.equal(f.calls[0].headers['tenant-id'], '124')
})
test('logged-out host never falls back to stale injected identity', async () => {
  const f = fixture(); f.setState({})
  await assert.rejects(f.api.syncAllIndexes(false), /从业务中台登录/)
  assert.equal(f.calls.length, 0)
})
test('refreshes both HTTP and business 401 once with updated Authorization', async () => {
  for (const response of [{ status: 401, data: {} }, { status: 200, data: { code: 401 } }]) {
    const f = fixture([response]); await f.api.getIndexSyncControl()
    assert.equal(f.renewals(), 1)
    assert.equal(f.calls[1].headers.Authorization, 'Bearer renewed')
  }
})
test('does not retry permission denial or mask it as empty results', async () => {
  const f = fixture([{ status: 200, data: { code: 403 } }])
  await assert.rejects(f.api.syncAllIndexes(true), /没有中台索引同步权限/)
  assert.equal(f.calls.length, 1); assert.equal(f.renewals(), 0)
})
test('rejects tenant changes while refreshing rather than submitting under a different tenant', async () => {
  const f = fixture([{ status: 401, data: {} }])
  f.window.__SQLBOT_REFRESH_LOGIN_STATE__ = async () => { f.setState({ token: 'other', tenantId: 999 }) }
  await assert.rejects(f.api.syncAllIndexes(false), /从业务中台登录/)
  assert.equal(f.calls.length, 1)
})
test('rejects late responses after logout and invalid proxy HTML', async () => {
  const f = fixture([logout => { logout(); return { status: 200, data: { code: 0, data: { enabled: false, time: '22:00' } } } }])
  await assert.rejects(f.api.getIndexSyncControl(), /登录状态已变化/)
  const html = fixture([{ status: 200, data: '<html>host page</html>' }])
  await assert.rejects(html.api.getIndexSyncControl(), /无法获取中台索引配置/)
})
test('persistent 401 stops after one renewal', async () => {
  const f = fixture([{ status: 401, data: {} }, { status: 401, data: {} }])
  await assert.rejects(f.api.getIndexSyncControl(), /登录已失效/)
  assert.equal(f.calls.length, 2)
})
test('manual sync and schedule use existing Data contracts', async () => {
  const f = fixture(); await f.api.syncAllIndexes(false)
  await f.api.saveIndexSchedule({ enabled: false, time: '21:30', force: false })
  assert.equal(f.calls[0].method, 'POST'); assert.equal(f.calls[0].url.endsWith('/all'), true)
  assert.equal(f.calls[0].data.force, false)
  assert.equal(f.calls[1].method, 'PUT'); assert.equal(f.calls[1].url.endsWith('/schedule'), true)
  assert.equal(f.calls[1].data.enabled, false)
})

test('task detail uses authenticated SQLBot request with current host token and tenant', async () => {
  const job = { jobId: 'job/1', datasourceId: 5, errors: [{ tableId: 123, code: 'METADATA_READ_FAILED' }] }
  const f = fixture([{ status: 200, data: job }])
  const signal = new AbortController().signal
  assert.equal(await f.api.getIndexJob(job.jobId, signal), job)
  assert.equal(f.calls.length, 1)
  assert.equal(f.calls[0].baseURL, '/api/v1')
  assert.equal(f.calls[0].url, '/platform/table-embeddings/sync/job%2F1')
  assert.equal(f.calls[0].headers.get('X-SQLBOT-TOKEN'), 'Bearer current')
  assert.equal(f.calls[0].headers.get('tenant-id'), '124')
  assert.equal(f.calls[0].signal, signal)
  assert.equal(f.calls[0].requestOptions.customError, true)
})

test('task detail renews once using the host identity and does not retry forbidden access', async () => {
  const job = { jobId: 'job-2', datasourceId: 5, errors: [] }
  const f = fixture([{ status: 401, data: {} }, { status: 200, data: job }])
  assert.equal(await f.api.getIndexJob('job-2'), job)
  assert.equal(f.renewals(), 1)
  assert.deepEqual(f.calls.map(call => call.headers.get('X-SQLBOT-TOKEN')), ['Bearer current', 'Bearer renewed'])
  const denied = fixture([{ status: 403, data: { detail: 'private internal error' } }])
  await assert.rejects(denied.api.getIndexJob('job-2'), error => error.response.status === 403)
  assert.equal(denied.calls.length, 1)
  assert.equal(denied.renewals(), 0)
})

test('task detail never falls back to old credentials after logout', async () => {
  const f = fixture([{ status: 401, data: {} }])
  f.setState({})
  f.window.__SQLBOT_REFRESH_LOGIN_STATE__ = async () => { throw new Error('Logged out') }
  await assert.rejects(f.api.getIndexJob('job-3'))
  assert.equal(f.calls.length, 1)
  assert.equal(f.calls[0].headers.get('X-SQLBOT-TOKEN'), undefined)
  assert.equal(f.calls[0].headers.get('tenant-id'), undefined)
})

test('failure details translate known reasons and never display arbitrary exception content', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/views/system/index-sync/indexFailure.ts'), 'utf8')
  const sandbox = { module: { exports: {} } }
  vm.runInNewContext(transformSync(source, { loader: 'ts', format: 'cjs' }).code, sandbox)
  const { indexFailureReason, indexFailureTable } = sandbox.module.exports
  assert.match(indexFailureReason('METADATA_READ_FAILED'), /元数据和数据源连接/)
  assert.match(indexFailureReason('TABLE_EMBEDDING_FAILED'), /Embedding/)
  assert.match(indexFailureReason('PROCESS_INTERRUPTED'), /服务中断/)
  assert.doesNotMatch(indexFailureReason('connect password=private-secret'), /private-secret|password/)
  assert.equal(indexFailureTable(123), '表 ID 123')
  assert.equal(indexFailureTable('password=private-secret'), '任务级错误')
  assert.match(indexFailureReason('__proto__'), /管理员/)
})
