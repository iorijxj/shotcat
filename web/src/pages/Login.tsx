import { useState } from 'react'
import { login } from '../lib/auth'

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function submit() {
    if (!username.trim() || !password) return setErr('请输入用户名和密码')
    setBusy(true)
    setErr('')
    try {
      await login(username.trim(), password)
      onSuccess()
    } catch (e: any) {
      setErr(e?.message || '登录失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="center" style={{ flexDirection: 'column', gap: 16 }}>
      <div className="modal" style={{ width: 320 }}>
        <div className="modal-h">登录</div>
        <label className="fld"><span>用户名</span>
          <input value={username} autoFocus onChange={(e) => setUsername(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()} />
        </label>
        <label className="fld"><span>密码</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()} />
        </label>
        {err && <div className="fld-err">{err}</div>}
        <div className="modal-foot">
          <button className="btn primary" disabled={busy} onClick={submit}>{busy ? '登录中…' : '登录'}</button>
        </div>
      </div>
    </div>
  )
}
