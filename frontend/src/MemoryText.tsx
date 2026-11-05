import { useEffect, useId, useState } from 'react'
import { Modal } from './Modal'
import { acceptTextSuggestion, clearSessionMemory, configureSessionMemory, deleteTextMemory, dismissTextSuggestion, memoryCapabilities, memorySettings, organizeTextMemory, regenerateMemory, request, saveTextMemory, saveTextMemoryBatch, sessionMemory, textMemoryItems, undoTextMemory, type MemorySuggestion, type SessionMemory, type TextMemoryItem } from './api'

const localTime = (value?: string | null) => { if (!value) return ''; const date = new Date(value); return Number.isNaN(date.getTime()) ? '' : new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16) }

export function TextSuggestion({ card, courseId, busy, run }: { card: MemorySuggestion; courseId: string; busy: boolean; run: (work: () => Promise<unknown>) => void }) {
  const fieldId = useId()
  const [maxChars, setMaxChars] = useState(1000)
  useEffect(() => { let active = true; void memoryCapabilities().then(value => { if (active) setMaxChars(value.max_content_chars) }).catch(() => {}); return () => { active = false } }, [])
  const [items, setItems] = useState(card.items ?? [])
  const [hidden, setHidden] = useState(false)
  const [replacementConfirmed, setReplacementConfirmed] = useState(false)
  const edited = JSON.stringify(items) !== JSON.stringify(card.items ?? [])
  const [key, setKey] = useState(() => crypto.randomUUID())
  if (card.status !== 'pending' || hidden) return null
  return <aside className="memory-card" aria-label="长期记忆建议"><strong>是否保存这些长期记忆？</strong><p>根据当前会话提出；确认前不会使用。</p>
    {items.map((item, i) => <div key={i}><label htmlFor={`${fieldId}-${i}`}>候选正文</label><textarea id={`${fieldId}-${i}`} value={item.content} onChange={e => { setKey(crypto.randomUUID()); setReplacementConfirmed(false); setItems(items.map((value, n) => n === i ? { ...value, content: e.target.value } : value)) }} /><span>{Array.from(item.content).length} / {maxChars} 字符</span><label><input type="checkbox" checked={item.course_id === null} onChange={e => { setKey(crypto.randomUUID()); setReplacementConfirmed(false); setItems(items.map((value, n) => n === i ? { ...value, course_id: e.target.checked ? null : courseId } : value)) }} />所有课程</label></div>)}
    {card.target && <p>将替换原记忆（版本 {card.target.version}）：{card.target.content}</p>}
    {edited && card.target && <label><input type="checkbox" checked={replacementConfirmed} onChange={e => setReplacementConfirmed(e.target.checked)} />确认用修改后的内容替换上述原记忆</label>}
    <div className="memory-actions"><button disabled={busy || (!!card.target && edited && !replacementConfirmed) || items.some(i => !i.content.trim() || Array.from(i.content).length > maxChars)} onClick={() => run(() => acceptTextSuggestion(card, items, key, replacementConfirmed))}>保存</button><button disabled={busy} onClick={() => run(() => dismissTextSuggestion(card, true))}>不再建议类似内容</button><button disabled={busy} onClick={() => run(() => dismissTextSuggestion(card, false))}>忽略本次</button><button onClick={() => setHidden(true)}>收起</button></div></aside>
}

export function TextMemoryPanel({ courseId, sessionId, onClose, onChanged }: { courseId: string; sessionId: string; onClose: () => void; onChanged: () => Promise<void> }) {
  const editorId = useId()
  const empty = (): TextMemoryItem => ({ content: '', course_id: courseId, use_mode: 'relevant' })
  const [data, setData] = useState<Awaited<ReturnType<typeof textMemoryItems>> | null>(null)
  const [editor, setEditor] = useState<TextMemoryItem>(empty)
  const [key, setKey] = useState(() => crypto.randomUUID())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [query, setQuery] = useState('')
  const [candidates, setCandidates] = useState<TextMemoryItem[]>([])
  const [summary, setSummary] = useState<SessionMemory | null>(null)
  const [details, setDetails] = useState<{ revisions: TextMemoryItem[]; sources: {kind: string; version: number; available: boolean; content?: string; name?: string}[] } | null>(null)
  const [undo, setUndo] = useState<string>('')
  const count = Array.from(editor.content).length
  const limit = data?.max_content_chars ?? 1000
  const load = async () => { const result = await textMemoryItems(courseId, query, true); setData(result); if (sessionId) setSummary(await sessionMemory(sessionId)) }
  useEffect(() => { let live = true; const refresh = async () => { try { const [result, current] = await Promise.all([textMemoryItems(courseId, query, true), sessionId ? sessionMemory(sessionId) : Promise.resolve(null)]); if (live) { setData(result); setSummary(current) } } catch (e) { if (live) setError(String(e)) } }; void refresh(); const timer = window.setInterval(() => { void refresh() }, 4000); return () => { live = false; window.clearInterval(timer) } }, [courseId, sessionId, query])
  const run = async (work: () => Promise<unknown>) => { setBusy(true); setError(''); try { const value = await work(); if (value && typeof value === 'object' && 'operation_id' in value) setUndo('status' in value && value.status === 'undone' ? '' : String(value.operation_id)); await load(); await onChanged() } catch (e) { setError(String(e)) } finally { setBusy(false) } }
  const control = editor.course_id === null ? data?.global_settings : data?.settings
  const changeControl = (name: 'use' | 'write' | 'suggest', value: boolean) => control && run(() => memorySettings({ course_id: editor.course_id, version: control.version, [name]: value }))
  const edit = (item: TextMemoryItem) => { setEditor(item); setCandidates([]); setKey(crypto.randomUUID()); setNotice('') }
  return <Modal title="长期记忆管理" className="memory-dialog" onClose={onClose} locked={busy}><div className="memory-panel">
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <p>{data?.enabled ? '明确要求可在聊天中保存；推断建议确认后生效。' : '服务端长期记忆写入未开启。'}</p>
    {control && <fieldset disabled={busy}><legend>当前编辑范围的开关</legend>{(['use', 'write', 'suggest'] as const).map(name => <label key={name}><input type="checkbox" checked={control[name]} onChange={e => void changeControl(name, e.target.checked)} />{{use:'使用长期记忆',write:'允许保存',suggest:'允许主动建议'}[name]}</label>)}</fieldset>}
    {data && (editor.course_id === null ? data.global_suppressions : data.suppressions).length > 0 && <div><p>已关闭类似建议：{(editor.course_id === null ? data.global_suppressions : data.suppressions).join('；')}</p><button disabled={busy} onClick={() => void run(() => request(`/memory/text-suppressions?version=${control?.version}${editor.course_id === null ? '' : '&course_id=' + encodeURIComponent(courseId)}`, { method: 'DELETE' }))}>重新允许这些建议</button></div>}
    <label>搜索记忆<input value={query} onChange={e => setQuery(e.target.value)} /></label>
    {data?.has_more && <p>结果超过500条，请缩小搜索范围。</p>}
    <ul>{data?.items.filter(item => item.content.toLocaleLowerCase().includes(query.toLocaleLowerCase())).map(item => <li key={item.id}><p>{item.content}</p><small>{item.effective_status && item.effective_status !== 'active' ? '已停用或失效 · ' : ''}{item.course_id ? '当前课程' : '所有课程'} · {item.use_mode === 'always' ? '持续参考' : '相关时使用'}</small><button disabled={busy} onClick={() => edit(item)}>编辑</button><button disabled={busy} onClick={() => void run(() => deleteTextMemory(item))}>删除</button><button disabled={busy} onClick={() => void run(async () => { const result = await request<NonNullable<typeof details>>(`/memory/items/${item.id}`); setDetails(result) })}>来源与版本</button></li>)}</ul>
    {details && <details open><summary>来源与版本</summary><ol>{details.revisions.map((revision, i) => <li key={i}>版本 {revision.version} · {revision.status}<p>{revision.content}</p></li>)}</ol>{details.sources.length === 0 && <p>由你在管理入口直接保存。</p>}{details.sources.map((source, i) => <div key={i}><small>版本 {source.version} · {{messages:'原始用户消息',sessions:'来源会话',attachments:'附件',documents:'课程资料',chunks:'资料片段',grants:'仓库授权',policies:'来源权限',answers:'保存回执'}[source.kind] ?? '来源'}</small><p>{source.available ? source.content ?? source.name ?? '来源有效' : '来源已失效，不再参与回答。'}</p></div>)}<button onClick={() => setDetails(null)}>收起详情</button></details>}
    <fieldset disabled={busy}><legend>{editor.id ? '编辑长期记忆' : '新增长期记忆'}</legend><label htmlFor={editorId}>正文</label><textarea id={editorId} value={editor.content} onChange={e => { setEditor({ ...editor, content: e.target.value }); setCandidates([]); setKey(crypto.randomUUID()) }} /><p role={count > limit ? 'alert' : 'status'}>{count} / {limit} 字符{count > limit ? '，已超限；原文保留，请整理后确认。' : ''}</p>
      <label><input type="checkbox" checked={editor.course_id === null} onChange={e => { setEditor({ ...editor, course_id: e.target.checked ? null : courseId }); setKey(crypto.randomUUID()) }} />所有课程</label>
      <label>使用方式<select value={editor.use_mode} onChange={e => setEditor({ ...editor, use_mode: e.target.value as TextMemoryItem['use_mode'] })}><option value="relevant">相关时使用</option><option value="always">持续参考</option></select></label>
      <label>适用条件<input value={editor.applies_when ?? ''} onChange={e => setEditor({ ...editor, applies_when: e.target.value || null })} /></label><label>例外<input value={editor.exceptions ?? ''} onChange={e => setEditor({ ...editor, exceptions: e.target.value || null })} /></label>
      <label>生效时间（本地时间）<input type="datetime-local" value={localTime(editor.valid_from)} onChange={e => setEditor({ ...editor, valid_from: e.target.value ? new Date(e.target.value).toISOString() : null })} /></label>
      <label>到期时间（本地时间）<input type="datetime-local" value={localTime(editor.expires_at)} onChange={e => setEditor({ ...editor, expires_at: e.target.value ? new Date(e.target.value).toISOString() : null })} /></label>
      <button disabled={!data?.enabled || !editor.content.trim() || count > limit} onClick={() => void run(async () => { const result = await saveTextMemory(editor, key); setNotice('已提交长期记忆。'); setEditor(empty()); setKey(crypto.randomUUID()); return result })}>保存</button>
      <button disabled={!editor.content.trim() || count > 12000 || !!editor.id} onClick={() => void run(async () => { const result = await organizeTextMemory(editor.content, key); setCandidates(result.items.map(item => ({ ...item, course_id: editor.course_id }))); setNotice('仅生成候选，尚未保存；请检查条件、数值和例外。') })}>帮助整理为多条</button><button onClick={() => edit(empty())}>清空编辑并新增</button>
    </fieldset>
    {candidates.length > 0 && <fieldset disabled={busy}><legend>整理预览：确认前不保存</legend>{candidates.map((item, i) => <label key={i}>候选正文<textarea value={item.content} onChange={e => setCandidates(candidates.map((value, n) => n === i ? { ...value, content: e.target.value } : value))} /></label>)}<button disabled={candidates.some(item => !item.content.trim() || Array.from(item.content).length > limit)} onClick={() => void run(async () => { const result = await saveTextMemoryBatch(candidates, key); setCandidates([]); setNotice('已整批保存整理结果。'); return result })}>确认并整批保存</button><button onClick={() => setCandidates([])}>取消整理，保留原文</button></fieldset>}
    {undo && <button disabled={busy} onClick={() => void run(async () => { const result = await undoTextMemory(undo); setUndo(''); return result })}>撤销最近操作</button>}
    {summary && <fieldset disabled={busy}><legend>当前会话滚动摘要</legend><label><input type="checkbox" disabled={!summary.available} checked={summary.enabled} onChange={e => void run(() => configureSessionMemory(sessionId, summary.version, e.target.checked))} />开启后自动维护</label><p>已覆盖 {summary.covered} 条消息 · {summary.updated_at ?? '尚未生成'}</p>{summary.blocked_reason && <p role="alert">摘要已暂停：必要原文超出预算。</p>}{summary.summary && <div>{Object.values(summary.summary).flat().map((item, i) => <p key={i}>{item.text}</p>)}</div>}<button disabled={!summary.enabled} onClick={() => void run(() => regenerateMemory(sessionId))}>重新生成</button><button onClick={() => void run(() => clearSessionMemory(sessionId, summary.version))}>清除并关闭摘要</button></fieldset>}
  </div></Modal>
}
