const assert = require('node:assert/strict')
const { test } = require('node:test')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const axios = require('axios')
const { transformSync } = require('esbuild')

function fixture({ embedded = true, renew = true, accept = 'fresh' } = {}) {
  const calls = []
  let current = { token: 'expired', tenantId: 124 }
  let refreshes = 0
  const cache = new Map([['user.token', 'stale-local']])
  const window = {
    __INJECTED_LOGIN_STATE__: { token: 'stale-snapshot' },
    __SQLBOT_GET_LOGIN_STATE__: () => current,
    __SQLBOT_REFRESH_LOGIN_STATE__: async () => {
      refreshes++
      await new Promise((resolve) => setTimeout(resolve, 5))
      if (!renew) throw new Error('refresh expired')
      current = { token: 'fresh', tenantId: 124 }
      return current
    },
  }
  const modules = {
    axios: { ...axios, create(config) {
      const client = axios.create(config)
      client.defaults.adapter = async (request) => {
        calls.push({ token: request.headers.get('X-SQLBOT-TOKEN'), tenant: request.headers.get('tenant-id') })
        const response = { data: { code: 0, data: 'ok' }, status: 200, statusText: 'OK', config: request, headers: {} }
        if (request.headers.get('X-SQLBOT-TOKEN') !== `Bearer ${accept}`) {
          response.status = 401
          throw new axios.AxiosError('Unauthorized', 'ERR_BAD_REQUEST', request, null, response)
        }
        return response
      }
      return client
    } },
    '@/utils/useCache': { useCache: () => ({ wsCache: cache }) },
    './utils': { getLocale: () => 'zh-CN' },
    '@/stores/assistant': { useAssistantStore: () => ({}) },
    'vue-router': { useRouter: () => ({}) },
    'json-bigint': { parse: JSON.parse },
    'vite-plugin-qiankun/es/helper': { qiankunWindow: { __POWERED_BY_QIANKUN__: embedded } },
  }
  const source = fs.readFileSync(path.join(__dirname, '../src/utils/request.ts'), 'utf8')
  const code = transformSync(source, { loader: 'ts', format: 'cjs', define: { 'import.meta.env.VITE_API_BASE_URL': '"/api/v1"' } }).code
  const sandbox = { window, console, setTimeout, fetch: async (_url, request) => {
    calls.push({ token: request.headers['X-SQLBOT-TOKEN'], tenant: request.headers['tenant-id'] })
    return { status: request.headers['X-SQLBOT-TOKEN'] === `Bearer ${accept}` ? 200 : 401 }
  }, module: { exports: {} }, require: (name) => {
    assert.ok(name in modules, name)
    return modules[name]
  } }
  vm.runInNewContext(code, sandbox)
  const request = sandbox.module.exports.request
  return {
    get: () => request.get('/system/aimodel', { requestOptions: { silent: true } }),
    stream: () => request.fetchStream('/chat/question', { question: 'test' }),
    setState: (state) => { current = state }, calls, refreshes: () => refreshes,
  }
}

test('stream uses current host token and tenant instead of injected snapshot', async () => {
  const f = fixture()
  f.setState({ token: 'fresh', tenantId: 124 })
  assert.equal((await f.stream()).status, 200)
  assert.deepEqual(f.calls, [{ token: 'Bearer fresh', tenant: '124' }])
})

test('stream renews through host once after 401', async () => {
  const f = fixture()
  assert.equal((await f.stream()).status, 200)
  assert.equal(f.refreshes(), 1)
  assert.deepEqual(f.calls.map(c => c.token), ['Bearer expired', 'Bearer fresh'])
})

test('stream does not reuse logged out credentials or retry endlessly', async () => {
  const f = fixture({ renew: false })
  f.setState({})
  assert.equal((await f.stream()).status, 401)
  assert.equal(f.calls[0].token, undefined)
  assert.equal(f.calls.length, 1)
  const retry = fixture({ accept: 'never-valid' })
  assert.equal((await retry.stream()).status, 401)
  assert.equal(retry.refreshes(), 1)
  assert.equal(retry.calls.length, 2)
})

test('configuration request reads the current host token and tenant, not mount snapshot', async () => {
  const f = fixture()
  f.setState({ token: 'fresh', tenantId: 124 })
  assert.equal(await f.get(), 'ok')
  assert.deepEqual(f.calls, [{ token: 'Bearer fresh', tenant: '124' }])
  assert.equal(f.refreshes(), 0)
})

test('expired token is renewed through host once and request retried', async () => {
  const f = fixture()
  assert.equal(await f.get(), 'ok')
  assert.equal(f.refreshes(), 1)
  assert.deepEqual(f.calls.map((c) => c.token), ['Bearer expired', 'Bearer fresh'])
})

test('parallel configuration requests share one renewal', async () => {
  const f = fixture()
  assert.deepEqual(await Promise.all([f.get(), f.get(), f.get()]), ['ok', 'ok', 'ok'])
  assert.equal(f.refreshes(), 1)
})

test('failed refresh remains unauthorized without retrying invalid credentials', async () => {
  const f = fixture({ renew: false })
  await assert.rejects(f.get())
  assert.equal(f.refreshes(), 1)
  assert.equal(f.calls.length, 1)
})

test('a second 401 cannot cause an infinite renewal loop', async () => {
  const f = fixture({ accept: 'never-valid' })
  await assert.rejects(f.get())
  assert.equal(f.refreshes(), 1)
  assert.equal(f.calls.length, 2)
})

test('standalone SQLBot uses its own token without calling the host', async () => {
  const f = fixture({ embedded: false, accept: 'stale-local' })
  assert.equal(await f.get(), 'ok')
  assert.equal(f.refreshes(), 0)
})

test('logged-out host does not fall back to stale local or injected credentials', async () => {
  const f = fixture({ renew: false })
  f.setState({})
  await assert.rejects(f.get())
  assert.equal(f.calls[0].token, undefined)
})
