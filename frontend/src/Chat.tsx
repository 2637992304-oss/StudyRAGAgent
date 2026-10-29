import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import './auto-tools.css'
import { cancelChat, revokeRepository, updateChatSession } from './api'
import { Icon } from './Icons'
import { readThinking, rememberThinking, thinkingLabel } from './thinking'
import { askChat, attachmentBlob, chatMessagePage, createChatSession, deleteAttachment, documentImageBlob, listAttachments, openDocument, saveAttachment, uploadAttachment, type Attachment, type ChatMessage, type ChatSession, type Citation, type Document } from './api'

type MarkdownNode = { type: string; value?: string; url?: string; children?: MarkdownNode[] }
function citationPlugin(citations: Citation[]) {
  return () => (tree: MarkdownNode) => {
    function walk(node: MarkdownNode) {
      if (!node.children || ['link', 'code', 'inlineCode', 'math', 'inlineMath'].includes(node.type)) return
      node.children = node.children.flatMap(child => {
        if (child.type !== 'text' || !child.value) { walk(child); return [child] }
        const parts: MarkdownNode[] = []
        const pattern = /(?<![A-Za-z0-9_\\])\[(\d+)\]/g
        let start = 0
        for (const match of child.value.matchAll(pattern)) {
          const citation = citations.find(c => c.label === Number(match[1]))
          if (!citation) continue
          if (match.index > start) parts.push({ type: 'text', value: child.value.slice(start, match.index) })
          parts.push({ type: 'link', url: `#source-${citation.citation_id}`, children: [{ type: 'text', value: `[${match[1]}]` }] })
          start = match.index + match[0].length
        }
        if (!parts.length) return [child]
        if (start < child.value.length) parts.push({ type: 'text', value: child.value.slice(start) })
        return parts
      })
    }
    walk(tree)
  }
}

function Answer({ message }: { message: ChatMessage }) {
  const normalized = message.content.replace(/\\\[/g, '$$').replace(/\\\]/g, '$$').replace(/\\\(/g, '$').replace(/\\\)/g, '$')
  return <div className="answer-text markdown"><ReactMarkdown remarkPlugins={[remarkGfm, remarkMath, citationPlugin(message.citations)]} rehypePlugins={[rehypeKatex]} skipHtml components={{ img: () => null, a: ({ href, children }) => <a href={href} target={href?.startsWith('#') ? undefined : '_blank'} rel="noopener noreferrer" onClick={event => {
    if (!href?.startsWith('#source-')) return
    event.preventDefault()
    const target = document.getElementById(href.slice(1))
    const disclosure = target?.closest('details')
    if (disclosure) disclosure.open = true
    requestAnimationFrame(() => { target?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); target?.focus({ preventScroll: true }) })
  }}>{children}</a> }}>{normalized}</ReactMarkdown></div>
}

function Source({ item, onError, onRevoke }: { item: Citation; onError: (value: string) => void; onRevoke: (id: string) => Promise<void> }) {
  const [imageUrl, setImageUrl] = useState('')
  useEffect(() => () => { if (imageUrl) URL.revokeObjectURL(imageUrl) }, [imageUrl])
  if (item.kind === 'web' || item.kind === 'github') return <article className="source-card" tabIndex={-1} id={`source-${item.citation_id}`}><strong>[{item.label}] {item.filename}</strong><div>{item.kind === 'web' ? '网页来源' : `仓库来源 · ${item.sha?.slice(0, 12)}`}{item.line_start ? ` · 行 ${item.line_start}–${item.line_end}` : ''}</div><small>访问时间：{item.accessed_at}{item.truncated ? ' · 正文已截断' : ''}</small><pre className="external-snippet">{item.snippet}</pre><a href={item.url} target="_blank" rel="noopener noreferrer">打开{item.kind === 'web' ? '网页' : '固定提交来源'}</a>{item.kind === 'github' && item.grant_id && <button type="button" className="text-button" onClick={() => onRevoke(item.grant_id!).catch(e => onError(String(e)))}>撤销此仓库访问</button>}</article>
  const location = item.page_start ? `第 ${item.page_start} 页` : item.slide_no ? `第 ${item.slide_no} 张幻灯片` : '原资料'
  return <article className="source-card" tabIndex={-1} id={`source-${item.citation_id}`}>
    <div><strong>[{item.label}] {item.filename}</strong><span>{item.document_type} · {location}</span></div>
    {item.section_path && <small>{item.section_path}</small>}<p>{item.snippet}</p>
    <button type="button" className="text-button" onClick={() => { if (/\.(jpe?g|png|webp)$/i.test(item.filename)) documentImageBlob(item.document_id).then(setImageUrl).catch(error => onError(String(error))); else openDocument(item.document_id, item.page_start).catch(error => onError(String(error))) }}>打开原文件</button>
    {imageUrl && <div className="image-modal" role="dialog" aria-modal="true" aria-label="来源原图"><button type="button" onClick={() => setImageUrl('')}>关闭原图</button><img src={imageUrl} alt={item.filename} /></div>}
  </article>
}

function Picture({ item, removable, onRemove, onSaved, onError }: { item: Attachment; removable: boolean; onRemove: () => void; onSaved: () => Promise<void>; onError: (s: string) => void }) {
  const [url, setUrl] = useState('')
  const [failed, setFailed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(Boolean(item.saved_document_id))
  useEffect(() => {
    let disposed = false
    let allocated = ''
    attachmentBlob(item.id).then(value => { allocated = value; if (disposed) URL.revokeObjectURL(value); else setUrl(value) }).catch(() => { if (!disposed) setFailed(true) })
    return () => { disposed = true; if (allocated) URL.revokeObjectURL(allocated) }
  }, [item.id])
  return <figure className="attachment-card">{url ? <a href={url} target="_blank" rel="noopener noreferrer"><img src={url} alt={item.filename} /></a> : <span>{failed ? '图片不可用' : '加载图片…'}</span>}<figcaption>{item.filename}</figcaption><div className="actions"><button type="button" disabled={saving || saved || failed} onClick={async () => { setSaving(true); try { await saveAttachment(item.id); setSaved(true); await onSaved() } catch (e) { onError(String(e)) } finally { setSaving(false) } }}>{saved ? '已保存到资料' : saving ? '保存中…' : '保存到课程资料'}</button>{removable && <button type="button" onClick={onRemove}>移除图片</button>}</div></figure>
}

type ChatProps = {
  accountId: string; courseId: string; courseName: string; session?: ChatSession; loadingSession: boolean;
  docs: Document[]; onSaved: () => Promise<void>; onSessionCreated: (session: ChatSession) => void;
  onSessionUpdated: (session: ChatSession) => void; onSessionsRefresh: () => void;
  onBusyChange: (busy: boolean) => void; onOpenSidebar: () => void;
}
function readDraft(key: string) {
  try { return sessionStorage.getItem(key) ?? '' } catch { return '' }
}
function messageOrder(a: ChatMessage, b: ChatMessage) {
  const milliseconds = Date.parse(a.created_at) - Date.parse(b.created_at)
  if (milliseconds) return milliseconds
  const fraction = (value: string) => (value.match(/\.(\d+)/)?.[1] ?? '').padEnd(6, '0')
  return fraction(a.created_at).localeCompare(fraction(b.created_at)) || a.id.localeCompare(b.id)
}
function mergeMessages(current: ChatMessage[], incoming: ChatMessage[]) {
  const rows = new Map(current.filter(m => !m.id.startsWith('pending-')).map(m => [m.id, m]))
  incoming.forEach(m => rows.set(m.id, m))
  return [...rows.values()].sort(messageOrder)
}
export function Chat({ accountId, courseId, courseName, session, loadingSession, docs, onSaved, onSessionCreated, onSessionUpdated, onSessionsRefresh, onBusyChange, onOpenSidebar }: ChatProps) {
  const sessionId = session?.id ?? ''
  const draftKey = `studyrag-draft-${accountId}-${courseId}-${sessionId || 'new'}`
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [historyCursor, setHistoryCursor] = useState<string | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const historyWorking = useRef(false)
  const prependScroll = useRef<{ height: number; top: number } | null>(null)
  const [drafts, setDrafts] = useState<Attachment[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>(session?.document_ids ?? [])
  const [question, setQuestion] = useState(() => readDraft(draftKey))
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState('')
  const [error, setError] = useState('')
  const [thinking, setThinking] = useState(() => readThinking(accountId))
  const abortRef = useRef<AbortController | null>(null)
  const imageInputRef = useRef<HTMLInputElement>(null)
  const questionRef = useRef<HTMLTextAreaElement>(null)
  const mounted = useRef(true)
  const [loading, setLoading] = useState(Boolean(sessionId))
  const [showBottom, setShowBottom] = useState(false)
  const [scopeOpen, setScopeOpen] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)
  const loadedSession = useRef('')
  const working = useRef(false)
  const readyDocs = docs.filter(item => item.status === 'ready')

  useEffect(() => {
    const textarea = questionRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`
  }, [question])

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; abortRef.current?.abort() } }, [])
  useEffect(() => { onBusyChange(busy); return () => onBusyChange(false) }, [busy, onBusyChange])
  useEffect(() => { setSelectedIds(session?.document_ids ?? []) }, [session])
  useEffect(() => {
    if (!loadingSession && !loading && !messages.length) questionRef.current?.focus({ preventScroll: true })
  }, [loadingSession, loading])
  useEffect(() => {
    if (!busy && sessionId) setQuestion(readDraft(draftKey))
    // Explicit conversation switches remount this view; a draft acquiring an ID must keep its text.
  }, [sessionId])
  useLayoutEffect(() => {
    const area = scrollRef.current
    if (area && prependScroll.current) {
      area.scrollTop = prependScroll.current.top + area.scrollHeight - prependScroll.current.height
      prependScroll.current = null
    } else if (area && followRef.current) area.scrollTop = area.scrollHeight
  }, [messages, progress])
  useEffect(() => {
    if (busy) return
    let disposed = false
    if (!sessionId) { setMessages([]); setDrafts([]); setLoading(false); return }
    if (loadedSession.current !== sessionId) setLoading(true)
    Promise.all([chatMessagePage(sessionId), listAttachments(sessionId)]).then(([history, files]) => { if (!disposed) { const first = loadedSession.current !== sessionId; setMessages(current => first ? history.items : mergeMessages(current, history.items)); if (first) setHistoryCursor(history.next_cursor); setDrafts(files.filter(a => !a.message_id)); loadedSession.current = sessionId } }).catch(cause => { if (!disposed) setError(String(cause)) }).finally(() => { if (!disposed) setLoading(false) })
    return () => { disposed = true }
  }, [sessionId, busy])

  async function loadEarlier() {
    if (!historyCursor || historyWorking.current || busy || working.current) return
    historyWorking.current = true; setHistoryLoading(true); setError('')
    try {
      const page = await chatMessagePage(sessionId, historyCursor)
      if (!mounted.current) return
      const area = scrollRef.current
      if (area) prependScroll.current = { height: area.scrollHeight, top: area.scrollTop }
      followRef.current = false
      setMessages(current => mergeMessages(current, page.items)); setHistoryCursor(page.next_cursor)
    } catch (cause) { if (mounted.current) setError(String(cause)) }
    finally { historyWorking.current = false; if (mounted.current) setHistoryLoading(false) }
  }
  async function refreshLatest(target: string) {
    const page = await chatMessagePage(target)
    if (!mounted.current) return
    if (loadedSession.current !== target) { setHistoryCursor(page.next_cursor); loadedSession.current = target }
    setMessages(current => mergeMessages(current, page.items))
  }
  async function revokeHistory(grantId: string) {
    if (busy || working.current || historyWorking.current) return
    working.current = true; setBusy(true); setError('')
    const target = sessionId, wanted = messages.length
    try {
      await revokeRepository(target, grantId)
      // Hide old answers immediately, then re-authorize every loaded page.
      if (!mounted.current) return
      setMessages(current => current.map(m => m.role === 'assistant' ? { ...m, content: '来源权限已变更，正在重新核对历史。', citations: [] } : m))
      let page = await chatMessagePage(target)
      let authorized = page.items
      while (page.next_cursor && authorized.length < wanted) {
        page = await chatMessagePage(target, page.next_cursor)
        authorized = mergeMessages(page.items, authorized)
      }
      if (mounted.current) { setMessages(authorized); setHistoryCursor(page.next_cursor) }
    } catch (cause) { if (mounted.current) setError(String(cause)) }
    finally { working.current = false; if (mounted.current) setBusy(false) }
  }
  async function ensureSession(): Promise<string> {
    if (sessionId) return sessionId
    const created = await createChatSession(courseId, selectedIds.length ? selectedIds : null)
    if (mounted.current) { try { sessionStorage.removeItem(draftKey) } catch { /* Optional draft persistence. */ }; onSessionCreated(created) }
    return created.id
  }
  async function changeScope(ids: string[]) {
    if (busy || working.current || historyWorking.current) return
    const previousIds = selectedIds
    setSelectedIds(ids)
    if (!sessionId) return
    working.current = true; setBusy(true); setError('')
    try { onSessionUpdated(await updateChatSession(sessionId, { document_ids: ids.length ? ids : null })); setSelectedIds(ids) }
    catch (e) { setSelectedIds(previousIds); setError(e instanceof Error ? e.message : String(e)) }
    finally { working.current = false; setBusy(false) }
  }
  async function addImages(files: File[]) {
    if (busy || loading || loadingSession || working.current || historyWorking.current || files.length === 0) return
    working.current = true
    setBusy(true); setError('')
    try {
      if (files.length + drafts.length > 4) throw new Error('每条消息最多附上4张图片，请分次发送。')
      const target = await ensureSession()
      for (const file of files) {
        const item = await uploadAttachment(target, file)
        if (mounted.current) setDrafts(current => [...current, item])
      }
    } catch (cause) { setError(String(cause)) } finally { working.current = false; if (mounted.current) setBusy(false) }
  }
  async function removeImage(item: Attachment) {
    setBusy(true); setError('')
    try { await deleteAttachment(item.id); setDrafts(current => current.filter(a => a.id !== item.id)); setMessages(current => current.map(m => ({ ...m, attachments: m.attachments?.filter(a => a.id !== item.id) }))) } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  async function send() {
    const content = question.trim()
    const requestThinking = thinking
    if ((!content && drafts.length === 0) || busy || loading || loadingSession || working.current || historyWorking.current) return
    working.current = true; followRef.current = true; setShowBottom(false)
    setError(''); setBusy(true); setProgress('正在理解问题…')
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const target = await ensureSession()
      const userMessage: ChatMessage = { id: `pending-user-${Date.now()}`, role: 'user', content, attachments: drafts, thinking: requestThinking, status: 'completed', error: null, citations: [], created_at: new Date().toISOString() }
      const assistantMessage: ChatMessage = { id: `pending-assistant-${Date.now()}`, role: 'assistant', content: '', thinking: requestThinking, status: 'pending', error: null, citations: [], created_at: new Date().toISOString() }
      setMessages(current => [...current, userMessage, assistantMessage]); setQuestion(''); setDrafts([])
      try { sessionStorage.removeItem(draftKey) } catch { /* Optional draft persistence. */ }
      let refreshedTitle = false
      await askChat(target, content, ({ event, data }) => {
        if (!mounted.current) return
        if (!refreshedTitle) { refreshedTitle = true; onSessionsRefresh() }
        if (event === 'answer_delta') { setProgress(''); setMessages(current => current.map((item, index) => index === current.length - 1 ? { ...item, content: item.content + String(data.text ?? '') } : item)) }
        if (event === 'citation') setMessages(current => current.map((item, index) => index === current.length - 1 ? { ...item, citations: [...item.citations, data as unknown as Citation] } : item))
        if (event === 'tool_status') setProgress(String(data.message ?? '正在处理…'))
      }, controller.signal, drafts.map(a => a.id), requestThinking)
      if (mounted.current) { await refreshLatest(target); setDrafts([]); onSessionsRefresh() }
    } catch (cause) {
      if (mounted.current) setError(controller.signal.aborted ? '已停止本次回答。' : cause instanceof Error ? cause.message : String(cause))
    } finally { working.current = false; if (mounted.current) { setBusy(false); setProgress('') }; abortRef.current = null }
  }
  const inputDisabled = busy || loading || loadingSession || historyLoading
  return <section className="chat-page" aria-label="课程对话">
    <header className="conversation-header">
      <button type="button" className="icon-button mobile-nav-button" aria-label="打开课程导航" onClick={onOpenSidebar}><Icon name="menu" size={21} /></button>
      <div className="conversation-title"><span>{courseName}</span><h1>{session?.title || '新对话'}</h1></div>
    </header>
    <div className="conversation-scroll" ref={scrollRef} onScroll={event => {
      const area = event.currentTarget
      followRef.current = area.scrollHeight - area.scrollTop - area.clientHeight < 64
      setShowBottom(!followRef.current)
    }}>
    <div className="chat-history" aria-live="polite">{historyCursor && <button type="button" disabled={historyLoading || busy} onClick={() => void loadEarlier()}>{historyLoading ? '正在加载…' : '加载更早消息'}</button>}{messages.length === 0 && <div className="conversation-empty"><h2>{loading || loadingSession ? '正在加载对话…' : '有什么想一起弄明白的？'}</h2><p>输入问题，或附上题目图片。</p></div>}{messages.map(item => <article key={item.id} className={`chat-message ${item.role}`}><strong>{item.role === 'user' ? '你' : '学习助手'}</strong><small className="thinking-history">{thinkingLabel(item.thinking)}</small>{item.role === 'assistant' ? <Answer message={item} /> : <p>{item.content}</p>}<div className="attachments">{item.attachments?.map(a => <Picture key={a.id} item={a} removable={!busy} onRemove={() => removeImage(a)} onSaved={onSaved} onError={setError} />)}</div>{item.status === 'pending' && <small>{progress || '正在生成…'}</small>}{item.status === 'failed' && <small className="job-error">{item.error ?? '回答失败，请重试'}</small>}{item.citations.length > 0 && <details className="source-disclosure"><summary>查看来源（{item.citations.length}）</summary><div className="sources">{item.citations.map(citation => <Source key={citation.citation_id} item={citation} onError={setError} onRevoke={revokeHistory} />)}</div></details>}</article>)}</div>
    </div>
    <div className="composer-dock">
      {showBottom && <button type="button" className="icon-button jump-bottom" aria-label="回到最新消息" title="回到最新消息" onClick={() => { followRef.current = true; scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' }); setShowBottom(false) }}><Icon name="down" /></button>}
      <details className="composer-scope" open={scopeOpen} onToggle={event => setScopeOpen(event.currentTarget.open)}>
        <summary>资料范围 · {selectedIds.length ? `已选 ${selectedIds.length} 份` : '整门课程'}</summary>
        <fieldset className="scope-picker" disabled={inputDisabled}><legend>选择本次对话可检索的资料</legend>
          <label><input type="checkbox" checked={!selectedIds.length} onChange={() => void changeScope([])} />整门课程</label>
          {readyDocs.map(item => <label key={item.id}><input type="checkbox" checked={selectedIds.includes(item.id)} onChange={event => void changeScope(event.target.checked ? [...selectedIds, item.id] : selectedIds.filter(id => id !== item.id))} />{item.filename}</label>)}
          {!readyDocs.length && <span className="scope-empty">还没有可检索资料，可从课程旁的书本图标上传。</span>}
        </fieldset>
      </details>
      {error && <p className="alert" role="alert">{error}</p>}
    <form className="chat-form" aria-label="聊天输入框" aria-busy={busy} onSubmit={event => { event.preventDefault(); send() }}>
      {drafts.length > 0 && <div className="attachments composer-attachments">{drafts.map(a => <Picture key={a.id} item={a} removable={!busy} onRemove={() => removeImage(a)} onSaved={onSaved} onError={setError} />)}</div>}
      <textarea ref={questionRef} aria-label="输入问题" placeholder="解释一个概念，或上传图片并说明你想了解什么…" value={question} onChange={event => { setQuestion(event.target.value); try { sessionStorage.setItem(draftKey, event.target.value) } catch { /* Typing still works without storage. */ } }} onPaste={event => { const files = Array.from(event.clipboardData.files).filter(f => f.type.startsWith('image/')); if (files.length) { event.preventDefault(); addImages(files) } }} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() } }} maxLength={2000} disabled={inputDisabled} />
      <div className="composer-toolbar">
        <input ref={imageInputRef} className="composer-file-input" aria-label="附上题目图片" type="file" accept="image/jpeg,image/png,image/webp" multiple disabled={inputDisabled} onChange={event => { addImages(Array.from(event.target.files ?? [])); event.target.value = '' }} />
        <button type="button" className="composer-icon-button composer-attach" aria-label="添加图片" title="添加图片（JPEG、PNG、WebP）" disabled={inputDisabled} onClick={() => imageInputRef.current?.click()}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
        </button>
        <div className="composer-actions">
          <button type="button" className={`thinking-toggle ${thinking ? 'active' : ''}`} aria-pressed={thinking} disabled={inputDisabled} onClick={() => { const next = !thinking; setThinking(next); rememberThinking(accountId, next) }}>深度思考</button>
          {busy && abortRef.current ? <button type="button" className="composer-icon-button composer-send" aria-label="停止" title="停止生成" onClick={async () => { try { await cancelChat(sessionId) } catch (e) { setError(String(e)) } finally { abortRef.current?.abort() } }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
          </button> : <button type="submit" className="composer-icon-button composer-send" aria-label="发送" title="发送" disabled={inputDisabled || (!question.trim() && drafts.length === 0)}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" /></svg>
          </button>}
        </div>
      </div>
    </form>
    </div>
  </section>
}
