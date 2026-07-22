// 登录态管理：token 存 localStorage，供 api.ts 统一挂 Authorization 头。
const TOKEN_KEY = 'duanju.auth_token'
const BASE = '/api/v1'

// 登录鉴权临时旁路（内部开发用，待接入平台统一认证后改回 false）：需与后端
// AUTH_DISABLED 保持一致，否则会出现前端不弹登录框、后端仍 401 的不一致。
const AUTH_DISABLED = true

export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY)

export const isLoggedIn = (): boolean => AUTH_DISABLED || !!getToken()

// 从 JWT 解析当前用户 id（payload.sub）。仅用于前端拼装 per-user 资源 id，
// 真正的鉴权在后端。
export function currentUserId(): string | null {
  const token = getToken()
  if (!token) return AUTH_DISABLED ? 'dev-local' : null
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(atob(base64)).sub ?? null
  } catch {
    return null
  }
}

export function logout(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export async function login(username: string, password: string): Promise<void> {
  const r = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const j = await r.json().catch(() => null)
  if (!r.ok) throw new Error(j?.message || '登录失败')
  localStorage.setItem(TOKEN_KEY, j.data.access_token)
}
