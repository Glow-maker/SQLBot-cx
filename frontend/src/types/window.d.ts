// 扩展Window接口，添加__INJECTED_LOGIN_STATE__属性
export declare global {
  interface Window {
    __POWERED_BY_QIANKUN__?: boolean
    __INJECTED_PUBLIC_PATH_BY_QIANKUN__?: string
    __INJECTED_LOGIN_STATE__?: {
      token: string
    }
    __SQLBOT_QIANKUN_PROPS__?: {
      sendToMain?: (type: string, data?: any) => void
      sendMessage?: (type: string, data?: any, to?: string) => void
    }
  }
}
