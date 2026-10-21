import { reactive } from 'vue'

// 站点配置（公开安全字段）。启动时拉取一次；失败时使用默认值，
// 不能让配置接口故障阻塞首页与搜索。
const DEFAULTS = {
  site: {
    name: 'SuPanSou',
    subtitle: '网盘资源聚合搜索',
    logo: '/favicon.svg',
  },
  notice: { enabled: false, content: '', version: '' },
  footer: {
    legal: true,
    note: '',
    repo_url: 'https://github.com/akai-do/supansou',
  },
  features: {
    aliso_ui: true,
    stream_search: true,
    demand_enabled: false,
    admin_v2: false,
    site_notice_enabled: true,
  },
}

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function merge(base, extra) {
  const out = clone(base)
  if (!extra || typeof extra !== 'object') return out
  for (const key of Object.keys(extra)) {
    const value = extra[key]
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      out[key] = merge(out[key] || {}, value)
    } else if (value !== undefined && value !== null) {
      out[key] = value
    }
  }
  return out
}

export const siteState = reactive({
  loaded: false,
  error: '',
  settings: clone(DEFAULTS),
})

export async function loadSiteSettings() {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 3000)
  try {
    const resp = await fetch('/api/public/settings', {
      signal: controller.signal,
      cache: 'no-store',
    })
    if (!resp.ok) throw new Error('HTTP ' + resp.status)
    const data = await resp.json()
    if (data && !data.error) {
      siteState.settings = merge(DEFAULTS, data)
    }
  } catch (e) {
    siteState.error = e && e.name === 'AbortError' ? 'timeout' : String(e.message || e)
  } finally {
    clearTimeout(timer)
    siteState.loaded = true
  }
  return siteState.settings
}

export function featureEnabled(name) {
  return !!siteState.settings.features?.[name]
}

export function siteName() {
  return siteState.settings.site?.name || 'SuPanSou'
}
