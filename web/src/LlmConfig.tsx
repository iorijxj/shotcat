import { useEffect, useState } from 'react'
import { api, type ProviderSupported } from './lib/api'

const CATEGORY_LABEL: Record<string, string> = {
  text: '文本模型（剧本拆解等）',
  image: '图片模型（关键帧生成）',
  video: '视频模型',
}

// 临时 LLM 配置入口：创建/更新一个 Provider + 按需的 Model，并设为全局默认。
// OpenAI 这一项的 Base URL 可自由改写，因此也覆盖"兼容 OpenAI 接口的第三方中转站"场景。
export default function LlmConfig() {
  const [open, setOpen] = useState(false)
  const [supported, setSupported] = useState<ProviderSupported[]>([])
  const [providerKey, setProviderKey] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [modelNames, setModelNames] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [ok, setOk] = useState('')

  useEffect(() => {
    if (!open) return
    api.llmSupportedProviders().then((list) => {
      setSupported(list)
      setProviderKey((k) => k || list[0]?.key || '')
    }).catch(() => setErr('获取供应商列表失败'))
  }, [open])

  const current = supported.find((p) => p.key === providerKey)

  useEffect(() => {
    if (!current) return
    setBaseUrl(current.default_base_url || '')
    setModelNames({})
    setErr('')
    setOk('')
  }, [providerKey]) // eslint-disable-line react-hooks/exhaustive-deps

  async function submit() {
    if (!current) return
    if (current.requires_api_key && !apiKey.trim()) return setErr('请输入 API Key')
    if (!baseUrl.trim()) return setErr('请输入 Base URL')
    const filled = current.supported_categories.filter((c) => modelNames[c]?.trim())
    if (filled.length === 0) return setErr('至少填写一个模型名称（文本/图片/视频任选）')
    setBusy(true)
    setErr('')
    setOk('')
    try {
      const providerId = `llmcfg_${current.key}`
      const providerBody = {
        name: current.display_name,
        base_url: baseUrl.trim(),
        description: '临时 LLM 配置（web/ 顶栏「LLM 配置」创建）',
        status: 'active',
        api_key: apiKey.trim(),
      }
      const providerExists = await api.llmGetProvider(providerId).then(() => true).catch(() => false)
      if (providerExists) await api.llmUpdateProvider(providerId, providerBody)
      else await api.llmCreateProvider({ id: providerId, ...providerBody })

      const touched: Record<string, string> = {}
      for (const cat of filled) {
        const name = modelNames[cat].trim()
        const modelId = `${providerId}_${cat}`
        const modelExists = await api.llmGetModel(modelId).then(() => true).catch(() => false)
        if (modelExists) await api.llmUpdateModel(modelId, { name })
        else await api.llmCreateModel({ id: modelId, name, category: cat, provider_id: providerId })
        touched[cat] = modelId
      }

      const settings = await api.llmGetModelSettings()
      await api.llmUpdateModelSettings({
        default_text_model_id: touched.text ?? settings.default_text_model_id,
        default_image_model_id: touched.image ?? settings.default_image_model_id,
        default_video_model_id: touched.video ?? settings.default_video_model_id,
        api_timeout: settings.api_timeout,
        log_level: settings.log_level,
      })
      setOk('保存成功，已设为默认模型')
    } catch (e: any) {
      setErr(e?.message || '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button className="btn ghost" onClick={() => setOpen(true)}>LLM 配置</button>
      {open && (
        <div className="modal-mask" onClick={() => !busy && setOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-h">LLM 配置（临时）</div>
            <label className="fld"><span>供应商</span>
              <select value={providerKey} onChange={(e) => setProviderKey(e.target.value)}>
                {supported.map((p) => <option key={p.key} value={p.key}>{p.display_name}</option>)}
              </select>
            </label>
            {providerKey === 'openai' && (
              <div className="muted" style={{ fontSize: 12, marginTop: -8, marginBottom: 13 }}>
                也可用于兼容 OpenAI 接口的第三方中转站：Base URL 换成中转地址即可。
              </div>
            )}
            <label className="fld"><span>API Key</span>
              <input type="password" value={apiKey} placeholder={current?.requires_api_key ? '必填' : '可选'}
                onChange={(e) => setApiKey(e.target.value)} />
            </label>
            <label className="fld"><span>Base URL</span>
              <input value={baseUrl} placeholder="https://..." onChange={(e) => setBaseUrl(e.target.value)} />
            </label>
            {current?.supported_categories.map((cat) => (
              <label className="fld" key={cat}><span>{CATEGORY_LABEL[cat] || cat}</span>
                <input value={modelNames[cat] || ''} placeholder="不需要就留空"
                  onChange={(e) => setModelNames({ ...modelNames, [cat]: e.target.value })} />
              </label>
            ))}
            {err && <div className="fld-err">{err}</div>}
            {ok && <div className="fld" style={{ marginBottom: 10 }}><span className="ok">{ok}</span></div>}
            <div className="modal-foot">
              <button className="btn ghost" disabled={busy} onClick={() => setOpen(false)}>关闭</button>
              <button className="btn primary" disabled={busy} onClick={submit}>{busy ? '保存中…' : '保存并设为默认'}</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
