import './public-path'
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.less'
import App from './App.vue'
import router from './router'
import { i18n } from './i18n'
import VueDOMPurifyHTML from 'vue-dompurify-html'
import { renderWithQiankun, qiankunWindow } from 'vite-plugin-qiankun/es/helper'
import { useCache } from './utils/useCache'
import { AssistantStore } from '@/stores/assistant'

// 扩展Window接口，添加__INJECTED_LOGIN_STATE__属性

const { wsCache } = useCache()
let app = null as any

function render(props = {} as any) {
  const { container, loginState } = props
  window.__SQLBOT_QIANKUN_PROPS__ = props
  // 处理从基座传递的登录态
  if (loginState && loginState.token) {
    // 将登录态存储到全局对象中，供 request.ts 使用
    window.__INJECTED_LOGIN_STATE__ = { token: loginState.token }
    // 同时存储到缓存中
    wsCache.set('user.token', loginState.token)
  }
  app = createApp(App)
  const pinia = createPinia()
  app.use(pinia)
  app.use(router)
  app.use(i18n)
  app.use(VueDOMPurifyHTML)
  // qiankun 子应用模式：告诉 assistantStore 进入"虚拟 assistant + 自动选数据源"模式
  // 以驱动 chat/index.vue 里 isCompletePage / selectAssistantDs 条件渲染出输入框
  if (qiankunWindow.__POWERED_BY_QIANKUN__) {
    const assistantStore = AssistantStore(pinia)
    assistantStore.setAssistant(true)
    assistantStore.setAutoDs(true)
    assistantStore.setHistory(true)
  }
  app.mount(container ? container.querySelector('#app') : '#app')
}

// 独立运行时
if (!qiankunWindow.__POWERED_BY_QIANKUN__) {
  render()
}

renderWithQiankun({
  bootstrap() {
    console.log('[vue] app bootstraped')
  },
  mount(props: any) {
    console.log('[vue] props from main framework', props)
    render(props)
  },
  update(props: any) {
    console.log('[vue] app update', props)
    window.__SQLBOT_QIANKUN_PROPS__ = {
      ...(window.__SQLBOT_QIANKUN_PROPS__ || {}),
      ...props,
    }
    // 当基座登录态更新时，同步更新子应用的登录态
    if (props.loginState && props.loginState.token) {
      // 更新全局对象中的登录态
      window.__INJECTED_LOGIN_STATE__ = { token: props.loginState.token }
      // 同时更新缓存中的登录态
      wsCache.set('user.token', props.loginState.token)
    }
  },
  unmount() {
    if (app) {
      app.unmount()
      app = null
    }
    delete window.__SQLBOT_QIANKUN_PROPS__
  },
})
// export async function bootstrap() {
//   console.log('[vue] vue app bootstraped')
// }

// export async function mount(props: any) {
//   render(props)
//   console.log('[vue] props from main framework', props)
// }

// export async function unmount() {
//   //   instance.unmount()
//   instance.$destroy()
//   instance.$el.innerHTML = ''
//   instance = null
// }

// import 'element-plus/dist/index.css'
// const app = createApp(App)
// const pinia = createPinia()

// app.use(pinia)
// app.use(router)
// app.use(i18n)
// app.use(VueDOMPurifyHTML)
// app.mount('#app')
