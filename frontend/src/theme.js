// 主题：亮/暗双主题，localStorage 记忆，默认跟随系统
const KEY = 'dps-theme'

export function getTheme() {
  const saved = localStorage.getItem(KEY)
  if (saved === 'dark' || saved === 'light') return saved
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark' : 'light'
}

export function applyTheme(t) {
  document.documentElement.classList.toggle('dark', t === 'dark')
  localStorage.setItem(KEY, t)
}

export function initTheme() {
  applyTheme(getTheme())
}

export function toggleTheme() {
  const next = getTheme() === 'dark' ? 'light' : 'dark'
  applyTheme(next)
  return next
}
