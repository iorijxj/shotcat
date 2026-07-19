// 登录态管理：token 存 localStorage，供 api.ts 统一挂 Authorization 头。
const TOKEN_KEY = 'duanju.auth_token'
const BASE = '/api/v1'

export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY)

export const isLoggedIn = (): boolean => !!getToken()

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
