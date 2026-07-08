import { useEffect, useMemo, useRef, useState } from 'react'
import { api, fileUrl, type AssetImageBatchStatus, type Entity, type Project } from '../lib/api'
import Lightbox from '../Lightbox'

const CATS = [
  { key: 'character', label: '角色' },
  { key: 'scene', label: '场景' },
  { key: 'prop', label: '道具' },
]
const DATA_CATS = [...CATS, { key: 'costume', label: '服装' }]
type EntityImage = { id: number; file_id: string; view_angle: string; name?: string }

const PROMPT_TEMPLATE_VERSION = 'prompt-clean-v10'
const DEFAULT_REALISTIC_STYLE = '电影感写实，统一中性影调，自然光影，细节清晰，设定集质感。'
const CLOTHING_RE = /(衣|服|上衣|下装|裤|裙|外套|衬衫|T恤|针织|毛衣|风衣|夹克|校服|制服|鞋|靴|帽|围巾|领口|袖|腰带|包|配饰|颜色|浅色|深色|白色|黑色|蓝色|灰色|棕色|米色|长裤|长裙|短裙)/
const NON_CLOTHING_RE = /(手拿|拿着|握着|捧着|背着|抱着|携带|旧笔记本|笔记本|书本|汽水|道具)/

function cleanCostumeText(value: string) {
  const parts = value
    .split(/[，,。；;、]+/)
    .map((part) => part.trim())
    .filter(Boolean)
    .filter((part) => CLOTHING_RE.test(part) && !NON_CLOTHING_RE.test(part))
  return parts.join('，')
}

function cleanCharacterAppearance(value: string) {
  const parts = value
    .split(/[，,。；;、]+/)
    .map((part) => part.trim())
    .filter(Boolean)
    .filter((part) => !CLOTHING_RE.test(part) && !NON_CLOTHING_RE.test(part))
  return parts.join('，')
}

export default function Cast({ project }: { project: Project | null }) {
  const [data, setData] = useState<Record<string, Entity[]>>({})
  const [tab, setTab] = useState('character')
  const [fresh, setFresh] = useState<Record<string, string>>({}) // 刚生成的 file_id
  const [busy, setBusy] = useState<string>('') // 正在生成的实体 id
  const [stage, setStage] = useState('')
  const [err, setErr] = useState('')
  const [batch, setBatch] = useState<AssetImageBatchStatus | null>(null)
  const [pipe, setPipe] = useState('') // 视觉词典生成中
  const [angles, setAngles] = useState<Record<string, EntityImage[]>>({}) // 实体全部角度图
  const [lb, setLb] = useState<string | null>(null)
  const [promptEdits, setPromptEdits] = useState<Record<string, string>>({})
  const [promptResetTick, setPromptResetTick] = useState(0)
  const cancelledRef = useRef(false) // 卸载后停止轮询/批量
  // 挂载时必须重置：React 18 StrictMode(dev) 会模拟卸载再挂载，ref 跨挂载保留，
  // 不重置的话所有轮询一进来就"已取消"（任务照发、前端秒放弃）
  useEffect(() => { cancelledRef.current = false; return () => { cancelledRef.current = true } }, [])

  const loadAll = () => {
    if (!project) return
    Promise.all(DATA_CATS.map((c) => api.entities(c.key, project.id).catch(() => [] as Entity[]))).then((lists) => {
      const map: Record<string, Entity[]> = {}
      DATA_CATS.forEach((c, i) => (map[c.key] = lists[i]))
      setData(map)
    })
  }
  useEffect(loadAll, [project])
  useEffect(() => {
    if (!project) return
    const versionKey = `shotcat:promptTemplateVersion:${project.id}`
    const editsKey = `shotcat:promptEdits:${project.id}`
    const savedVersion = localStorage.getItem(versionKey)
    if (savedVersion !== PROMPT_TEMPLATE_VERSION) {
      localStorage.removeItem(editsKey)
      localStorage.setItem(versionKey, PROMPT_TEMPLATE_VERSION)
      setPromptEdits({})
      return
    }
    const savedPrompts = localStorage.getItem(editsKey)
    setPromptEdits(savedPrompts ? JSON.parse(savedPrompts) : {})
  }, [project?.id])

  useEffect(() => {
    if (!project) return
    localStorage.setItem(`shotcat:promptEdits:${project.id}`, JSON.stringify(promptEdits))
  }, [project?.id, promptEdits])

  useEffect(() => {
    if (!project) return
    const batchId = localStorage.getItem(`shotcat:assetImageBatch:${project.id}`)
    if (!batchId || batch) return
    let stopped = false
    ;(async () => {
      try {
        const first = await api.assetImageBatchStatus(batchId)
        if (stopped) return
        if (first.status === 'succeeded' || first.status === 'failed' || first.status === 'cancelled') {
          localStorage.removeItem(`shotcat:assetImageBatch:${project.id}`)
          await loadAll()
          return
        }
        setBatch(first)
        const done = await api.pollAssetImageBatch(
          batchId,
          (s) => !stopped && setBatch(s),
          () => stopped || cancelledRef.current,
        )
        if (!stopped && done) {
          localStorage.removeItem(`shotcat:assetImageBatch:${project.id}`)
          setErr(done.failed ? `批量生成完成，失败 ${done.failed} 项。` : '批量生成完成。')
          await loadAll()
          setBatch(null)
        }
      } catch {
        localStorage.removeItem(`shotcat:assetImageBatch:${project.id}`)
      }
    })()
    return () => { stopped = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id])

  const items = data[tab] ?? []

  const loadEntityImages = async (type: string, id: string) => {
    const imgs = await api.entityImages(type, id).catch(() => [])
    setAngles((m) => ({ ...m, [id]: imgs.filter((x: any) => x.file_id) }))
  }

  const setEntityNameLocal = (type: string, id: string, name: string) => {
    setData((m) => ({
      ...m,
      [type]: (m[type] ?? []).map((x) => x.id === id ? { ...x, name } : x),
    }))
  }

  // 当前 tab 实体的全部角度图（BACK/DETAIL 等多角度参考也要能看到）
  useEffect(() => {
    let stale = false
    ;(async () => {
      const m: Record<string, EntityImage[]> = {}
      for (const e of items) {
        const imgs = await api.entityImages(tab, e.id).catch(() => [])
        m[e.id] = imgs.filter((x: any) => x.file_id)
      }
      if (!stale) setAngles(m)
    })()
    return () => { stale = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, data])
  const roleLabel = useMemo(() => CATS.find((c) => c.key === tab)?.label ?? '', [tab])
  const thumbOf = (e: Entity) => (fresh[e.id] ? fileUrl(fresh[e.id]) : e.thumbnail || '')
  const visualDesc = (e: Entity | null) => (e?.description || '').split('【表演基线】')[0].trim()

  const styleClause = () => {
    return project?.visual_style === '动漫'
      ? '【风格】统一动画/动漫渲染，一致的线条与上色、统一角色比例；'
      : `【风格】${DEFAULT_REALISTIC_STYLE}`
  }
  const DESIGN_PREFIX: Record<string, string> = {
    character: '三视图画面：正面、侧面、背面横向并排，纯净中性背景，清晰展示外貌、发型、身形比例和服装细节。',
    actor: '演员形象设定图，半身或全身，纯净中性背景，清晰展示五官与形象细节，设定集风格。主体：',
    scene: '空无一人的场景环境：',
    prop: '道具设计图，纯净中性背景，主体居中，完整清晰展示道具的整体形态、材质与特定细节(刻痕/锈迹/文字等)，产品/设定集视角。道具：',
  }
  const designPrompt = (type: string, e: Entity) => {
    let body = visualDesc(e)
    if (!body) return ''
    let costumeText = ''
    if (type === 'character' && e.costume_id) {
      const cos = (data['costume'] ?? []).find((c) => c.id === e.costume_id)
      costumeText = cos?.description ? cleanCostumeText(visualDesc(cos)) : ''
      body = cleanCharacterAppearance(body) || visualDesc(e)
    }
    if (type === 'scene') {
      return [(DESIGN_PREFIX[type] || '') + body, styleClause()].filter(Boolean).join(' ')
    }
    if (type === 'character') {
      return [
        DESIGN_PREFIX[type],
        `角色外貌：${body}`,
        costumeText ? `服装：${costumeText}` : '',
        styleClause(),
      ].filter(Boolean).join(' ')
    }
    return [(DESIGN_PREFIX[type] || '') + body, styleClause()].filter(Boolean).join(' ')
  }
  const promptKey = (type: string, id: string) => (
    `${type}:${PROMPT_TEMPLATE_VERSION}:${id}`
  )
  const promptFor = (type: string, e: Entity) => {
    const saved = promptEdits[promptKey(type, e.id)]
    return saved ?? designPrompt(type, e)
  }
  const setPromptFor = (type: string, e: Entity, value: string) => {
    setPromptEdits((m) => ({ ...m, [promptKey(type, e.id)]: value }))
  }
  const clearSavedPrompts = () => {
    if (!project) return
    Object.keys(localStorage)
      .filter((key) => key.startsWith('shotcat:promptEdits:'))
      .forEach((key) => localStorage.removeItem(key))
    localStorage.setItem(`shotcat:promptTemplateVersion:${project.id}`, PROMPT_TEMPLATE_VERSION)
    setPromptEdits({})
    setPromptResetTick((v) => v + 1)
    setErr('已清除旧提示词，并按最新干净模板重建。')
  }
  const ensureSceneGuard = (prompt: string) => (
    prompt
  )

  async function renameEntity(e: Entity, value: string) {
    const name = value.trim()
    if (!name) {
      await loadAll()
      return
    }
    setEntityNameLocal(tab, e.id, name)
    try {
      await api.updateEntity(tab, e.id, { name })
      await loadEntityImages(tab, e.id)
    } catch (x: any) {
      setErr(x?.message || '名称保存失败')
      await loadAll()
      await loadEntityImages(tab, e.id)
    }
  }

  async function gen(e: Entity) {
    if (busy) return
    setBusy(e.id); setErr(''); setStage('生成中…')
    try {
      const prompt = (tab === 'scene' ? ensureSceneGuard(promptFor(tab, e)) : promptFor(tab, e)).trim()
      if (!prompt) throw new Error('提示词为空，请先填写后再生成')
      const fid = await api.generateEntityImage(tab, e.id, prompt, (p) => setStage(`生成中… ${p}%`), () => cancelledRef.current)
      setFresh((m) => ({ ...m, [e.id]: fid }))
      await loadEntityImages(tab, e.id)
    } catch (x: any) {
      setErr(`${e.name}：${x?.message || '生成失败'}`)
    } finally {
      setBusy(''); setStage('')
    }
  }
  async function genMissing() {
    if (!project || batch) return
    setErr('')
    setStage('提交任务中…')
    try {
      const queue: { type: string; id: string; name: string; image_id: number; prompt: string }[] = []
      for (const c of CATS) {
        for (const e of data[c.key] ?? []) {
          if (cancelledRef.current) break
          const prompt = promptFor(c.key, e).trim()
          if (!prompt || thumbOf(e)) continue
          const image_id = await api.ensureImageSlot(c.key, e.id)
          queue.push({ type: c.key, id: e.id, name: e.name, image_id, prompt })
        }
      }
      if (!queue.length) {
        setErr('没有可提交的缺失造型：需要未生成图片，且提示词不为空。')
        return
      }
      const created = await api.createAssetImageBatch(queue)
      localStorage.setItem(`shotcat:assetImageBatch:${project.id}`, created.batch_id)
      setBatch({
        batch_id: created.batch_id,
        status: 'queued',
        total: created.total,
        queued: created.total,
        running: 0,
        succeeded: 0,
        failed: 0,
        items: [],
      })
      const done = await api.pollAssetImageBatch(
        created.batch_id,
        (s) => setBatch(s),
        () => cancelledRef.current,
      )
      if (done) {
        localStorage.removeItem(`shotcat:assetImageBatch:${project.id}`)
        setErr(done.failed ? `批量生成完成，失败 ${done.failed} 项。` : '批量生成完成。')
        await loadAll()
      }
    } catch (x: any) {
      setErr(x?.message || '批量提交失败')
    } finally {
      setStage('')
      setBatch(null)
    }
  }
  async function lockVisualDict() {
    if (!project || pipe) return
    setPipe('dict'); setErr('')
    try {
      const job = await api.runPipeline('visual-dict', project.id)
      await api.pollPipeline(job, 200, () => cancelledRef.current)
      loadAll()
    } catch (x: any) { setErr(x?.message || '视觉词典生成失败') } finally { setPipe('') }
  }

  if (!project) return <div className="center">未找到项目 · 请先用 bridge 导入剧本</div>

  return (
    <div className="work">
      <div className="work-head">
        <h1>造型</h1>
        <div className="spacer" />
        <button className="btn ghost" disabled={!!pipe || !!busy} onClick={lockVisualDict}>
          {pipe === 'dict' ? '锁定中…（读全剧本）' : '① 锁定视觉词典'}
        </button>
        <button className="btn primary" disabled={!!batch || !!busy || !!pipe} onClick={genMissing}>
          {batch ? `排队生成 ${batch.succeeded + batch.failed}/${batch.total}` : stage === '提交任务中…' ? '提交任务中…' : '② 提交全部缺失造型任务'}
        </button>
      </div>

      <div className="tabs">
        {CATS.map((c) => (
          <button key={c.key} className={'tab' + (c.key === tab ? ' on' : '')} onClick={() => { setTab(c.key); setErr('') }}>
            {c.label} <span className="cnt">{(data[c.key] ?? []).length}</span>
          </button>
        ))}
      </div>

      <div className="tone-panel">
        <div className="tone-head">
          <div>
            <div className="tone-title">提示词管理</div>
            <div className="tone-sub">这里显示的是每张设定图最终会送去生成的提示词；艺术指导已前置到剧本抽取和视觉词典阶段。</div>
          </div>
          <button className="btn ghost" onClick={clearSavedPrompts} disabled={!!busy || !!batch}>清除旧提示词</button>
        </div>
      </div>

      {err && <div className="fld-err" style={{ marginBottom: 12 }}>{err}</div>}

      <div className="cast-grid">
        {items.map((e) => {
          const url = thumbOf(e)
          const busyThis = busy === e.id
          const currentPrompt = promptFor(tab, e)
          const canGenerate = !!currentPrompt.trim()
          return (
            <div className="cast-card" key={e.id}>
              <div className="cc-img">
                {url ? (
                  <img className="zoomable" src={url} alt={e.name} title="点击放大" onClick={() => setLb(url)} />
                ) : busyThis ? (
                  <div className="cc-ph"><div className="plus">◔</div>{stage}</div>
                ) : (
                  <div className="cc-ph"><span>○ 未生成</span></div>
                )}
              </div>
              {(angles[e.id]?.length ?? 0) > 0 && (
                <div className="cc-angles">
                  {angles[e.id].map((im) => (
                    <img key={im.id} src={fileUrl(im.file_id)} alt={im.view_angle} title={`${im.name || im.view_angle} · 点击放大`}
                      onClick={() => setLb(fileUrl(im.file_id))} />
                  ))}
                </div>
              )}
              <div className="cc-body">
                <div className="cc-h">
                  <input
                    className="n cc-name-input"
                    value={e.name}
                    disabled={!!busy || !!batch}
                    onChange={(ev) => setEntityNameLocal(tab, e.id, ev.target.value)}
                    onBlur={(ev) => renameEntity(e, ev.target.value)}
                  />
                  <span className="role">{roleLabel}</span>
                  <span className="id">{e.id.split('__').pop()}</span>
                </div>
                <div className="cc-desc">{visualDesc(e) || '（未锁定，先跑「① 锁定视觉词典」）'}</div>
                <div className="cc-prompt">
                  <div className="prompt-label">生成提示词{tab === 'scene' ? ` · ${PROMPT_TEMPLATE_VERSION}` : ''}</div>
                  <textarea
                    key={`${promptResetTick}:${tab}:${e.id}`}
                    className="prompt-edit"
                    value={currentPrompt}
                    disabled={busyThis || !!batch}
                    onChange={(ev) => setPromptFor(tab, e, ev.target.value)}
                    placeholder="未锁定设定时不会自动生成提示词；请先跑视觉词典，或手动填写。"
                  />
                  <div className="prompt-actions">
                    <button className="btn ghost" disabled={busyThis || !!batch} onClick={() => setPromptFor(tab, e, designPrompt(tab, e))}>
                      按当前基调重置
                    </button>
                  </div>
                </div>
                <button className="btn primary" style={{ width: '100%' }} disabled={!!busy || !!batch || !canGenerate} onClick={() => gen(e)}>
                  {busyThis ? (stage || '生成中…') : url ? '重新生成造型图' : '生成造型图'}
                </button>
              </div>
            </div>
          )
        })}
        {items.length === 0 && <div className="muted" style={{ padding: 20 }}>该分类暂无资产</div>}
      </div>

      <Lightbox url={lb} onClose={() => setLb(null)} />
    </div>
  )
}
