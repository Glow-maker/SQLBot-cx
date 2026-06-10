import { qiankunWindow } from 'vite-plugin-qiankun/es/helper'

if (qiankunWindow.__POWERED_BY_QIANKUN__) {
  const injectedPublicPath = qiankunWindow.__INJECTED_PUBLIC_PATH_BY_QIANKUN__

  // 设置相对路径的基准
  let baseElement = document.querySelector('base')
  if (!baseElement) {
    baseElement = document.createElement('base')
    document.head.appendChild(baseElement)
  }
  if (injectedPublicPath) {
    baseElement.setAttribute('href', injectedPublicPath)
  }
}
