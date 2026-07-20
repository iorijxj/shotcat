// 登录态管理：token 存 localStorage，供 api.ts 统一挂 Authorization 头。
const TOKEN_KEY = 'duanju.auth_token'
const BASE = '/api/v1'

export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY)

export const isLoggedIn = (): boolean => !!getToken()

// 从 JWT 解析当前用户 id（payload.sub）。仅用于前端拼装 per-user 资源 id，
// 真正的鉴权在后端。
export function currentUserId(): string | null {
  const token = getToken()
  if (!token) return null
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
