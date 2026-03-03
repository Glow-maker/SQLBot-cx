import { ElMessage } from 'element-plus-secondary'
import { useCache } from '@/utils/useCache'
import { useAppearanceStoreWithOut } from '@/stores/appearance'
import { useUserStore } from '@/stores/user'
import { request } from '@/utils/request'
import type { Router } from 'vue-router'
import { generateDynamicRouters } from './dynamic'
import { toLoginPage } from '@/utils/utils'

const appearanceStore = useAppearanceStoreWithOut()
const userStore = useUserStore()
const { wsCache } = useCache()
const whiteList = ['/login', '/admin-login']
const assistantWhiteList = ['/assistant', '/embeddedPage', '/embeddedCommon', '/401']
const externalTokenQueryKeys = ['x_sqlbot_token', 'sqlbot_token', 'token', 'access_token']

const wsAdminRouterList = ['/ds/index', '/as/index']
export const watchRouter = (router: Router) => {
  router.beforeEach(async (to: any, from: any, next: any) => {
    await loadXpackStatic()
    await appearanceStore.setAppearance()
    LicenseGenerator.generateRouters(router)
    let token = wsCache.get('user.token')
    const tokenFromQuery = pickExternalTokenFromRoute(to)
    if (!token && tokenFromQuery) {
      token = tokenFromQuery
      userStore.setToken(tokenFromQuery)
      clearExternalTokenFromBrowserUrl()
      const cleanedQuery = clearExternalTokenQuery(to?.query)
      next({
        path: to.path,
        query: cleanedQuery,
        hash: to.hash,
        replace: true,
      })
      return
    }

    if (token && to.path.startsWith('/login')) {
      if (!userStore.getUid) {
        try {
          await userStore.info()
          generateDynamicRouters(router)
        } catch (error) {
          console.warn('Failed to restore user info from token', error)
          userStore.clear()
          token = ''
        }
      }
      if (token && userStore.getUid) {
        next(to?.query?.redirect || '/')
        return
      }
    }

    if (assistantWhiteList.includes(to.path)) {
      next()
      return
    }
    if (whiteList.includes(to.path)) {
      next()
      return
    }
    if (!token) {
      // ElMessage.error('Please login first')
      next(toLoginPage(to.fullPath))
      return
    }
    if (!userStore.getUid) {
      try {
        await userStore.info()
      } catch (error) {
        console.warn('Failed to get user info, redirect to login', error)
        userStore.clear()
        next(toLoginPage(to.fullPath))
        return
      }
      generateDynamicRouters(router)
      const isFirstDynamicPath = to?.path && ['/ds/index', '/as/index'].includes(to.path)
      if (isFirstDynamicPath) {
        if (userStore.isSpaceAdmin) {
          next({ ...to, replace: true })
          return
        }
      }
    }
    if (to.path === '/docs') {
      location.href = to.fullPath
      return
    }
    if (to.path === '/' || accessCrossPermission(to)) {
      next('/chat')
      return
    }
    if (to.path === '/login' || to.path === '/admin-login') {
      console.info(from)
      next('/chat')
    } else {
      next()
    }
  })
}

const pickExternalTokenFromRoute = (to: any): string => {
  const query = to?.query || {}
  for (const key of externalTokenQueryKeys) {
    const value = query[key]
    const token = normalizeBearerToken(value)
    if (token) {
      return token
    }
  }
  const searchParams = new URLSearchParams(window.location.search)
  for (const key of externalTokenQueryKeys) {
    const token = normalizeBearerToken(searchParams.get(key))
    if (token) {
      return token
    }
  }
  return ''
}

const clearExternalTokenQuery = (query: Record<string, any>): Record<string, any> => {
  const cleanQuery = { ...(query || {}) }
  for (const key of externalTokenQueryKeys) {
    delete cleanQuery[key]
  }
  return cleanQuery
}

const clearExternalTokenFromBrowserUrl = () => {
  const currentUrl = new URL(window.location.href)
  let changed = false
  for (const key of externalTokenQueryKeys) {
    if (currentUrl.searchParams.has(key)) {
      currentUrl.searchParams.delete(key)
      changed = true
    }
  }
  if (changed) {
    window.history.replaceState({}, '', currentUrl.toString())
  }
}

const normalizeBearerToken = (value: unknown): string => {
  if (value === null || value === undefined) {
    return ''
  }
  const rawValue = Array.isArray(value) ? value[0] : value
  if (typeof rawValue !== 'string') {
    return ''
  }
  const text = decodeURIComponent(rawValue).trim()
  if (!text) {
    return ''
  }
  if (text.toLowerCase().startsWith('bearer ')) {
    return text.slice(7).trim()
  }
  return text
}

const accessCrossPermission = (to: any) => {
  if (!to?.path) return false
  return (
    (to.path.startsWith('/system') && !userStore.isAdmin) ||
    (to.path.startsWith('/set') && !userStore.isSpaceAdmin) ||
    (isWsAdminRouter(to) && !userStore.isSpaceAdmin)
  )
}

const isWsAdminRouter = (to?: any) => {
  return wsAdminRouterList.some((item: string) => to?.path?.startsWith(item))
}
const loadXpackStatic = () => {
  if (document.getElementById('sqlbot_xpack_static')) {
    return Promise.resolve()
  }
  const url = `/xpack_static/license-generator.umd.js?t=${Date.now()}`
  return new Promise((resolve, reject) => {
    request
      .loadRemoteScript(url, 'sqlbot_xpack_static', () => {
        LicenseGenerator?.init(import.meta.env.VITE_API_BASE_URL).then(() => {
          resolve(true)
        })
      })
      .catch((error) => {
        console.error('Failed to load xpack_static script:', error)
        ElMessage.error('Failed to load license generator script')
        reject(error)
      })
  })
}
