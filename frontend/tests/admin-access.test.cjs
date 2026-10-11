const assert = require('node:assert/strict')
const { test } = require('node:test')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { transformSync } = require('esbuild')
const { createPinia, defineStore } = require('pinia')

function fixture(response) {
  const pinia = createPinia()
  const cache = new Map()
  const api = { info: async () => response }
  const modules = {
    pinia: { defineStore },
    '@/api/login': { AuthApi: api },
    '@/utils/useCache': { useCache: () => ({ wsCache: cache }) },
    '@/i18n': { i18n: { global: { locale: { value: 'zh-CN' } } } },
    './index': { store: pinia },
    '@/utils/utils': {},
  }
  const source = fs.readFileSync(path.join(__dirname, '../src/stores/user.ts'), 'utf8')
  const code = transformSync(source, { loader: 'ts', format: 'cjs' }).code
  const sandbox = { module: { exports: {} }, require: (name) => {
    assert.ok(name in modules, name)
    return modules[name]
  } }
  vm.runInNewContext(code, sandbox)
  return { store: sandbox.module.exports.useUserStore(), api }
}

test('platform admin with non-local ID can open system configuration', async () => {
  const { store } = fixture({ id: 132, account: 'admin', isAdmin: true, weight: 1 })
  await store.info()
  assert.equal(store.isAdmin, true)
})

test('local SQLBot admin retains system configuration access', async () => {
  const { store } = fixture({ id: 1, account: 'admin', isAdmin: true })
  await store.info()
  assert.equal(store.isAdmin, true)
})

test('an ID or account name does not override server denial', async () => {
  const { store } = fixture({ id: 1, account: 'admin', isAdmin: false })
  await store.info()
  assert.equal(store.isAdmin, false)
})

test('missing or string admin flags do not grant access', async () => {
  for (const isAdmin of [undefined, 'false', 'true', 1]) {
    const { store } = fixture({ id: 132, account: 'admin', isAdmin })
    await store.info()
    assert.equal(store.isAdmin, false)
  }
})

test('identity refresh and logout clear administrator access', async () => {
  const { store, api } = fixture({ id: 132, isAdmin: true })
  await store.info()
  assert.equal(store.isAdmin, true)
  api.info = async () => ({ id: 200, isAdmin: false })
  await store.info()
  assert.equal(store.isAdmin, false)
  api.info = async () => ({ id: 132, isAdmin: true })
  await store.info()
  store.clear()
  assert.equal(store.isAdmin, false)
})
