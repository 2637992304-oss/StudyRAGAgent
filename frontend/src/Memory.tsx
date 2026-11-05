import { useEffect, useState } from 'react'
import { Modal } from './Modal'
import { acceptMemory, clearSessionMemory, configureSessionMemory, deleteMemoryPreference, dismissMemory, memoryPreferences, memorySettings, memorySuggestions, regenerateMemory, saveMemoryPreference, sessionMemory, undoMemory, type ChatMessage, type MemoryPreferences, type MemorySuggestion, type SessionMemory } from './api'
import './memory.css'
import { memoryCapabilities, undoTextMemory } from './api'
import { TextMemoryPanel, TextSuggestion } from './MemoryText'

const labels: Record<string, string> = { response_language: '讲解语言', code_language: '代码语言', explanation_detail: '讲解详细程度', hint_first: '先给提示' }
const values: Record<string, string[]> = { response_language: ['zh', 'en'], code_language: ['Python', 'Java', 'JavaScript', 'TypeScript', 'C', 'C++', 'SQL'], explanation_detail: ['brief', 'standard', 'detailed'], hint_first: ['true', 'false'] }
const text: Record<string, string> = { zh: '中文', en: '英文', brief: '简短', standard: '适中', detailed: '详细', true: '是', false: '否' }
const display = (value: string) => text[value] ?? value

function SuggestionCard({ card, courseId, busy, run }: { card: MemorySuggestion; courseId: string; busy: boolean; run: (work: () => Promise<unknown>) => void }) {
  const [edit, setEdit] = useState(false)
  const [value, setValue] = useState(card.value)
  const [global, setGlobal] = useState(card.scope === 'global')
  const [hidden, setHidden] = useState(false)
  if (card.schema_version === 2) return <TextSuggestion card={card} courseId={courseId} busy={busy} run={run} />
  if (hidden || card.status !== 'pending') return null
  return <aside className="memory-card" aria-label="记忆建议"><strong>是否记住这个偏好？</strong><p>{labels[card.key]}：{display(value)} · {global ? '所有课程' : '当前课程'}</p>
    {edit && <div><label>偏好值<select value={value} onChange={e => setValue(e.target.value)}>{(values[card.key] ?? []).map(v => <option key={v} value={v}>{display(v)}</option>)}</select></label><label><input type="checkbox" checked={global} onChange={e => setGlobal(e.target.checked)} />所有课程</label></div>}
    <div className="memory-actions"><button disabled={busy} onClick={() => run(async () => { const prefs = await memoryPreferences(courseId); return acceptMemory(card, global ? null : courseId, value, prefs.versions[`${global ? 'global' : `course:${courseId}`}:${card.key}`] ?? 0) })}>保存</button><button disabled={busy} onClick={() => setEdit(!edit)}>修改</button><button disabled={busy} onClick={() => run(() => dismissMemory(card, true))}>不再建议</button><button disabled={busy} aria-label="忽略本次建议" onClick={() => setHidden(true)}>关闭</button></div>
  </aside>
}

export function MemoryCards({ message, courseId, disabled, onChanged, onManage }: { message: ChatMessage; courseId: string; disabled: boolean; onChanged: () => Promise<void>; onManage: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const run = async (work: () => Promise<unknown>) => {
    setBusy(true); setError('')
    try { await work(); await onChanged() } catch (e) { setError(String(e)); await onChanged() } finally { setBusy(false) }
  }
  return <div className="memory-results">{message.memory?.operations.map(op => <aside className="memory-receipt" key={op.operation_id}><span>{op.status === 'saved' ? '已记住' : op.status === 'deleted' ? '已删除记忆' : op.status === 'undone' ? '操作已撤销' : op.status === 'conflict' ? '整批未保存' : '操作已失效'}：{op.schema_version === 2 ? op.items?.map(item => { const prior = op.before?.find(value => value?.id === item.id); return `${prior && prior.content !== item.content ? prior.content + ' → ' : ''}${item.content} · ${item.course_id ? '当前课程' : '所有课程'}${item.status === 'superseded' ? '（已替代）' : item.status === 'deleted' ? '（已删除）' : ''}` }).join('；') : `${labels[op.key]} · ${display(op.value)}`}{op.schema_version !== 2 && <> · {op.scope === 'global' ? '所有课程' : '当前课程'}</>}{op.detail ? ` · ${op.detail}` : ''}</span><button disabled={disabled || busy || !['saved', 'deleted'].includes(op.status)} onClick={() => void run(() => (op.schema_version === 2 ? undoTextMemory(op.operation_id) : undoMemory(op)))}>撤销</button><button disabled={disabled || busy} onClick={onManage}>管理</button></aside>)}
    {message.memory?.suggestions.map(card => <SuggestionCard key={card.id} card={card} courseId={courseId} busy={busy || disabled} run={work => void run(work)} />)}{error && <p role="alert">{error}</p>}</div>
}

function LegacyMemoryPanel({ courseId, sessionId, onClose, onChanged }: { courseId: string; sessionId: string; onClose: () => void; onChanged: () => Promise<void> }) {
  const [prefs, setPrefs] = useState<MemoryPreferences | null>(null)
  const [summary, setSummary] = useState<SessionMemory | null>(null)
  const [cards, setCards] = useState<MemorySuggestion[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [global, setGlobal] = useState(false)
  const [key, setKey] = useState('code_language')
  const [value, setValue] = useState('Python')
  const fetchData = () => Promise.all([memoryPreferences(courseId), sessionId ? sessionMemory(sessionId) : Promise.resolve(null), sessionId ? memorySuggestions(sessionId) : Promise.resolve([])])
  const load = async () => { const [p, s, c] = await fetchData(); setPrefs(p); setSummary(s); setCards(c) }
  useEffect(() => { let current = true; const fetch = () => fetchData().then(([p, s, c]) => { if (current) { setPrefs(p); setSummary(s); setCards(c) } }).catch(e => { if (current) setError(String(e)) }); void fetch(); const timer = window.setInterval(() => { void fetch() }, 3000); return () => { current = false; window.clearInterval(timer) } }, [courseId, sessionId])
  const run = async (work: () => Promise<unknown>) => { setBusy(true); setError(''); try { await work(); await load(); await onChanged() } catch (e) { setError(String(e)); await load() } finally { setBusy(false) } }
  const control = global ? prefs?.global_settings : prefs?.settings
  const scope = global ? 'global' : `course:${courseId}`
  return <Modal title="记忆管理" className="memory-dialog" onClose={onClose} locked={busy}><div className="memory-panel">
    {error && <p role="alert">{error}</p>}
    {prefs && <><p>{prefs.enabled ? '偏好记忆可用' : '服务端偏好功能未开启；仍可查看和删除已有记录'}</p><label><input type="checkbox" checked={global} onChange={e => setGlobal(e.target.checked)} />管理所有课程（不选则为当前课程）</label>
      {control && <fieldset disabled={busy}><legend>偏好开关</legend>{(['use', 'write', 'suggest'] as const).map(name => <label key={name}><input type="checkbox" checked={control[name]} onChange={e => void run(() => memorySettings({ course_id: global ? null : courseId, version: control.version, [name]: e.target.checked }))} />{{ use: '使用已保存偏好', write: '允许保存偏好', suggest: '允许主动建议' }[name]}</label>)}{control.suppressed.length > 0 && <button onClick={() => void run(() => memorySettings({ course_id: global ? null : courseId, version: control.version, suppressed: [] }))}>重新允许已关闭的建议类型</button>}</fieldset>}
      <p>当前有效偏好：{Object.entries(prefs.effective).map(([k, v]) => `${labels[k]}：${display(v)}`).join('；') || '无'}</p>
      <ul>{prefs.items.filter(p => p.scope === scope).map(p => <li key={p.id}>{labels[p.key]}：{display(p.value)} <button disabled={busy} onClick={() => { setKey(p.key); setValue(p.value) }}>编辑</button><button disabled={busy} onClick={() => void run(() => deleteMemoryPreference(p.id, p.version))}>删除</button></li>)}</ul>
      <fieldset disabled={busy || !prefs.enabled}><legend>保存偏好</legend><label>类型<select value={key} onChange={e => { setKey(e.target.value); setValue(values[e.target.value][0]) }}>{Object.entries(labels).map(([k, label]) => <option key={k} value={k}>{label}</option>)}</select></label><label>值<select value={value} onChange={e => setValue(e.target.value)}>{values[key].map(v => <option key={v} value={v}>{display(v)}</option>)}</select></label><button onClick={() => void run(() => saveMemoryPreference({ course_id: global ? null : courseId, key, value, version: prefs.versions[`${scope}:${key}`] ?? 0 }))}>保存偏好</button></fieldset>
    </>}
    {cards.some(c => c.status === 'pending') && <section aria-label="当前会话待确认建议"><h3>待确认建议</h3>{cards.map(card => <SuggestionCard key={card.id} card={card} courseId={courseId} busy={busy} run={work => void run(work)} />)}</section>}
    {summary && <fieldset disabled={busy}><legend>当前会话摘要</legend><label><input type="checkbox" disabled={!summary.available} checked={summary.enabled} onChange={e => void run(() => configureSessionMemory(sessionId, summary.version, e.target.checked))} />开启后自动维护</label><p>已覆盖 {summary.covered} 条消息；{summary.updated_at ?? '尚未生成'}。摘要可能遗漏，不作为资料来源。</p><p role="status">{summary.blocked_reason ? '摘要已暂停：单轮用户原文超出摘要输入预算。原始对话仍保留，可调整摘要预算后重新生成。' : ''}</p><p>{summary.jobs.slice(-3).map(j => `${j.status}（尝试${j.attempts}次）`).join('；')}</p>{summary.summary && Object.entries(summary.summary).map(([k, items]) => <div key={k}>{items.map((item, i) => <p key={i}>{item.text}</p>)}</div>)}<button disabled={!summary.enabled || !summary.available} onClick={() => void run(() => regenerateMemory(sessionId))}>重新生成</button><button onClick={() => void run(() => clearSessionMemory(sessionId, summary.version))}>清除并关闭摘要</button></fieldset>}
  </div></Modal>
}

export function MemoryPanel(props: { courseId: string; sessionId: string; onClose: () => void; onChanged: () => Promise<void> }) {
  const [engine, setEngine] = useState<string | null>(null)
  const [error, setError] = useState('')
  useEffect(() => { let live = true; void memoryCapabilities().then(value => { if (live) setEngine(value.engine) }).catch(e => { if (live) setError(String(e)) }); return () => { live = false } }, [props.courseId, props.sessionId])
  if (error) return <Modal title="长期记忆" onClose={props.onClose}><p role="alert">{error}</p></Modal>
  if (!engine) return <p role="status">正在读取记忆能力…</p>
  return engine !== 'legacy' ? <TextMemoryPanel key={props.courseId + props.sessionId} {...props} /> : <LegacyMemoryPanel {...props} />
}
