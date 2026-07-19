import { writeFileSync } from 'node:fs'

const baseUrl = (process.env.OPENAPI_BASE_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '')
const target = `${baseUrl}/openapi.json`

try {
  const res = await fetch(target)
  if (!res.ok) {
    console.error(`拉取 openapi 失败: ${target} 返回 HTTP ${res.status}`)
    process.exit(1)
  }
  writeFileSync('./openapi.json', await res.text())
} catch (err) {
  console.error(`无法连接后端 ${target}: ${err.message}`)
  console.error('提示: 后端未在该地址运行时,可通过环境变量 OPENAPI_BASE_URL 指定,例如 http://<WSL_IP>:8000')
  process.exit(1)
}
