// 登录态与统一请求封装：token 持久化 localStorage，401 自动登出回登录页。
import { computed, ref } from 'vue'

export const token = ref(localStorage.getItem('kg_token') || '')
export const username = ref(localStorage.getItem('kg_user') || '')
export const authed = computed(() => !!token.value)

export async function login(user, password) {
  const r = await fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: user, password }),
  })
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail
    throw new Error(detail || `登录失败（HTTP ${r.status}）`)
  }
  const body = await r.json()
  token.value = body.token
  username.value = body.username
  localStorage.setItem('kg_token', body.token)
  localStorage.setItem('kg_user', body.username)
}

export async function logout() {
  try {
    await apiFetch('/api/logout', { method: 'POST' })
  } catch { /* 本地登出不受影响 */ }
  clearAuth()
}

function clearAuth() {
  token.value = ''
  username.value = ''
  localStorage.removeItem('kg_token')
  localStorage.removeItem('kg_user')
}

export async function apiFetch(url, options = {}) {
  const r = await fetch(url, {
    ...options,
    headers: { ...(options.headers || {}),
               Authorization: `Bearer ${token.value}` },
  })
  if (r.status === 401) {
    clearAuth()
    throw new Error('登录已过期，请重新登录')
  }
  return r
}
