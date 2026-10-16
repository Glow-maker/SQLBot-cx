const assert = require('node:assert/strict')
const { test } = require('node:test')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript } = require('@vue/compiler-sfc')
const { transformSync } = require('esbuild')

async function renderLayout(isAdmin, routePath, operator = false) {
  const source = fs.readFileSync(path.join(__dirname, '../src/components/layout/LayoutDsl.vue'), 'utf8')
  const { descriptor } = parse(source)
  const script = compileScript(descriptor, { id: 'admin-layout-test', inlineTemplate: true })
  const code = transformSync(script.content, { loader: 'ts', format: 'cjs' }).code
  const modules = {
    vue,
    'vue-router': { useRoute: () => ({ path: routePath }), useRouter: () => ({ push() {} }) },
    '@/stores/user': { useUserStore: () => ({ isAdmin }) },
    '@/stores/operations': { useOperationsStore: () => ({ allowed: operator, openFeedback() {} }) },
    '@/components/operations/FeedbackDialog.vue': { __esModule: true, default: { render: () => null } },
    '@/utils/useEmitt': { useEmitt() {} },
    '@/utils/utils': { isMobile: () => false },
    './Menu.vue': { __esModule: true, default: { render: () => vue.h('div', '管理菜单') } },
  }
  const sandbox = { module: { exports: {} }, require: (name) => {
    assert.ok(name in modules, name)
    return modules[name]
  } }
  vm.runInNewContext(code, sandbox)
  const app = vue.createSSRApp(sandbox.module.exports.default)
  app.component('ElButton', { render() { return vue.h('button', this.$slots.default?.()) } })
  app.component('RouterView', { render: () => vue.h('main', '页面内容') })
  return renderToString(app)
}

test('administrator sees configuration entry and can return from system pages', async () => {
  const chat = await renderLayout(true, '/chat/index')
  assert.ok(chat.includes('系统配置'))
  const system = await renderLayout(true, '/system/model')
  assert.ok(system.includes('系统管理菜单'))
  assert.ok(system.includes('返回问数'))
})

test('ordinary users have no admin entry or configuration sidebar', async () => {
  for (const route of ['/chat/index', '/system/model']) {
    const html = await renderLayout(false, route)
    assert.ok(!html.includes('管理员配置入口'))
    assert.ok(!html.includes('系统管理菜单'))
  }
})

test('an operator gets an operations entry without the legacy administrator title', async () => {
  const html = await renderLayout(false, '/chat/index', true)
  assert.ok(html.includes('问数运营'))
  assert.ok(!html.includes('系统配置'))
  assert.ok(html.includes('问题反馈'))
})
