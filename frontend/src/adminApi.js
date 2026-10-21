// 后台请求封装：同源 session cookie、统一错误、401 跳登录。
const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function parse(resp) {
  try {
    return await resp.json()
  } catch (e) {
    return {}
  }
}

export async function adminFetch(path, options = {}) {
  const opts = { credentials: 'same-origin', ...options }
  if (opts.body !== undefined && typeof opts.body !== 'string') {
    opts.body = JSON.stringify(opts.body)
    opts.headers = { ...JSON_HEADERS, ...(opts.headers || {}) }
  }
  const resp = await fetch('/api' + path, opts)
  const data = await parse(resp)

  if (resp.status === 401 && path !== '/auth/login') {
    window.location.assign('/admin/login')
    const err = new Error(data.error || '请先登录')
    err.status = 401
    throw err
  }
  if (!resp.ok) {
    const err = new Error(data.error || `请求失败（HTTP ${resp.status}）`)
    err.status = resp.status
    err.data = data
    throw err
  }
  return data
}

export async function fetchMe() {
  try {
    const resp = await fetch('/api/auth/me', { credentials: 'same-origin' })
    if (!resp.ok) return null
    const data = await resp.json()
    return data.user || null
  } catch (e) {
    return null
  }
}

export async function login(username, password) {
  const resp = await fetch('/api/auth/login', {
    method: 'POST',
    credentials: 'same-origin',
    headers: JSON_HEADERS,
    body: JSON.stringify({ username, password }),
  })
  const data = await parse(resp)
  if (!resp.ok) {
    const err = new Error(data.error || '登录失败')
    err.status = resp.status
    throw err
  }
  return data
}

export async function logout() {
  try {
    await fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'same-origin',
    })
  } catch (e) { /* 忽略网络错误，前端照常回登录页 */ }
}
