import { qiankunWindow } from 'vite-plugin-qiankun/es/helper'

if (qiankunWindow.__POWERED_BY_QIANKUN__) {
  // 对于Vite项目，设置全局的base路径
  if (import.meta.env) {
    ;(import.meta.env as any).BASE_URL = qiankunWindow.__INJECTED_PUBLIC_PATH_BY_QIANKUN__
  }
  // 设置相对路径的基准
  let baseElement = document.querySelector('base')
  if (!baseElement) {
    baseElement = document.createElement('base')
    document.head.appendChild(baseElement)
  }
  baseElement.setAttribute('href', qiankunWindow.__INJECTED_PUBLIC_PATH_BY_QIANKUN__)
}
