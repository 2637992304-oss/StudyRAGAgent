import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { chatSessions, createChatSession, deleteChatSession, documents, updateChatSession, type ChatSession, type Course, type Document, type User } from './api'
import { Chat } from './Chat'
import { CourseLibrary } from './CourseLibrary'
import { CreateCourseDialog } from './CreateCourseDialog'
import { Icon } from './Icons'
import { Modal } from './Modal'
import { WorkspaceLayout } from './WorkspaceLayout'
import { ThemeToggle } from './ThemeToggle'
import './workspace.css'

type Navigation = { courseId?: string; sessions?: Record<string, string> }
function readNavigation(accountId: string): Navigation {
  try { return JSON.parse(localStorage.getItem(`studyrag-navigation-${accountId}`) ?? '{}') as Navigation } catch { return {} }
}

function RenameDialog({ session, onClose, onSaved }: { session: ChatSession; onClose: () => void; onSaved: (session: ChatSession) => void }) {
  const [title, setTitle] = useState(session.title || '新对话')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const working = useRef(false)
  useEffect(() => { inputRef.current?.focus(); inputRef.current?.select() }, [])
  return <Modal title="重命名对话" onClose={onClose} locked={busy}>
    <form onSubmit={async event => {
      event.preventDefault(); if (!title.trim() || working.current) return
      working.current = true; setBusy(true); setError('')
      try { onSaved(await updateChatSession(session.id, { title: title.trim() })); onClose() }
      catch (e) { setError(e instanceof Error ? e.message : String(e)) }
      finally { working.current = false; setBusy(false) }
    }}>
      <label className="course-name-field">对话名称<input ref={inputRef} value={title} maxLength={120} required disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
      {error && <p className="alert" role="alert">{error}</p>}
      <div className="course-dialog-footer"><button disabled={busy || !title.trim()}>{busy ? '保存中…' : '保存'}</button></div>
    </form>
  </Modal>
}

export function StudyWorkspace({ user, initialCourses, initialNotice, onLogout }: { user: User; initialCourses: Course[]; initialNotice: string; onLogout: () => void }) {
  const [navigation] = useState(() => readNavigation(user.id))
  const [courseList, setCourseList] = useState(initialCourses)
  const [courseId, setCourseId] = useState(() => initialCourses.find(item => item.id === navigation.courseId)?.id ?? initialCourses[0]?.id ?? '')
  const selections = useRef<Record<string, string>>(navigation.sessions ?? {})
  const [sessionId, setSessionId] = useState(selections.current[courseId] ?? '')
  const [sessions, setSessions] = useState<Record<string, ChatSession[]>>({})
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(courseId ? [courseId] : []))
  const [loading, setLoading] = useState(Boolean(courseId))
  const [docs, setDocs] = useState<Document[]>([])
  const [busy, setBusy] = useState(false)
  const [creating, setCreating] = useState(false)
  const [view, setView] = useState(0)
  const [createOpen, setCreateOpen] = useState(false)
  const [library, setLibrary] = useState<Course | null>(null)
  const [rename, setRename] = useState<ChatSession | null>(null)
  const [menu, setMenu] = useState<{ session: ChatSession; x: number; y: number } | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState(initialNotice)
  const [mobileSidebar, setMobileSidebar] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const createLock = useRef(false)
  const activeCourse = useRef(courseId)
  activeCourse.current = courseId
  const currentCourse = courseList.find(item => item.id === courseId)
  const currentSession = sessions[courseId]?.find(item => item.id === sessionId)
  const locked = busy || creating

  const loadSessions = useCallback(async (id: string) => {
    const list = await chatSessions(id)
    setSessions(current => ({ ...current, [id]: list }))
    return list
  }, [])
  const refreshDocuments = useCallback(async () => {
    if (!courseId) return
    const list = await documents(courseId)
    if (activeCourse.current === courseId) setDocs(list)
  }, [courseId])
  const libraryChanged = useCallback((id: string) => { if (id === courseId) void refreshDocuments().catch(e => setError(String(e))) }, [courseId, refreshDocuments])
  useEffect(() => {
    let disposed = false
    if (!courseId) { setLoading(false); return }
    setLoading(true); setDocs([]); setError('')
    loadSessions(courseId).then(list => {
      if (!disposed) setSessionId(current => list.some(item => item.id === current) ? current : list[0]?.id ?? '')
    }).catch(e => { if (!disposed) setError(String(e)) }).finally(() => { if (!disposed) setLoading(false) })
    void refreshDocuments().catch(e => { if (!disposed) setError(String(e)) })
    return () => { disposed = true }
  }, [courseId, loadSessions, refreshDocuments])
  useEffect(() => {
    if (loading) return
    selections.current[courseId] = sessionId
    try { localStorage.setItem(`studyrag-navigation-${user.id}`, JSON.stringify({ courseId, sessions: selections.current })) } catch { /* Navigation still works without storage. */ }
  }, [courseId, sessionId, user.id, loading])
  useEffect(() => {
    if (!docs.some(item => item.status === 'queued' || item.status === 'processing')) return
    const id = window.setInterval(() => { void refreshDocuments().catch(() => undefined) }, 2000)
    return () => window.clearInterval(id)
  }, [docs, refreshDocuments])
  useEffect(() => {
    if (!notice) return
    const id = window.setTimeout(() => setNotice(''), 4500)
    return () => window.clearTimeout(id)
  }, [notice])
  useEffect(() => {
    if (!mobileSidebar) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape' && !document.querySelector('dialog[open]')) setMobileSidebar(false) }
    document.addEventListener('keydown', close)
    return () => document.removeEventListener('keydown', close)
  }, [mobileSidebar])
  useEffect(() => {
    if (!menu) return
    menuRef.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const dismiss = (event: MouseEvent) => { if (!menuRef.current?.contains(event.target as Node)) setMenu(null) }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenu(null) }
    document.addEventListener('mousedown', dismiss); document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('mousedown', dismiss); document.removeEventListener('keydown', escape) }
  }, [menu])
  function updateSession(session: ChatSession) {
    setSessions(current => ({ ...current, [session.course_id]: (current[session.course_id] ?? []).map(item => item.id === session.id ? session : item) }))
  }
  function selectCourse(item: Course) {
    if (locked) return
    setExpanded(current => { const next = new Set(current); if (next.has(item.id)) next.delete(item.id); else next.add(item.id); return next })
    if (courseId !== item.id) {
      setCourseId(item.id); setSessionId(selections.current[item.id] ?? sessions[item.id]?.[0]?.id ?? ''); setView(value => value + 1)
    }
  }
  function selectSession(item: ChatSession) {
    if (locked) return
    if (sessionId !== item.id || courseId !== item.course_id) { setCourseId(item.course_id); setSessionId(item.id); setView(value => value + 1) }
    setMobileSidebar(false)
  }
  function createdSession(item: ChatSession) {
    setSessions(current => ({ ...current, [item.course_id]: [item, ...(current[item.course_id] ?? []).filter(existing => existing.id !== item.id)] }))
    setSessionId(item.id); setExpanded(current => new Set(current).add(item.course_id))
  }
  async function newSession(item: Course) {
    if (locked || loading || createLock.current) return
    createLock.current = true; setCreating(true); setError('')
    try {
      const session = await createChatSession(item.id, null)
      selections.current[item.id] = session.id
      setCourseId(item.id); createdSession(session); setView(value => value + 1); setMobileSidebar(false)
    } catch (e) { setError(String(e)) }
    finally { createLock.current = false; setCreating(false) }
  }
  function showMenu(item: ChatSession, x: number, y: number) {
    setMenu({ session: item, x: Math.min(Math.max(8, x), window.innerWidth - 168), y: Math.min(Math.max(8, y), window.innerHeight - 100) })
  }
  async function removeSession(item: ChatSession) {
    setMenu(null)
    if (locked || !window.confirm(`删除对话「${item.title || '新对话'}」及聊天图片？已保存的课程资料会保留。`)) return
    setCreating(true)
    try {
      await deleteChatSession(item.id)
      const list = await loadSessions(item.course_id)
      if (sessionId === item.id) { setSessionId(list[0]?.id ?? ''); setView(value => value + 1) }
    } catch (e) { setError(String(e)) } finally { setCreating(false) }
  }
  return <WorkspaceLayout mobileSidebarOpen={mobileSidebar} onCloseSidebar={() => setMobileSidebar(false)} sidebar={<>
    <div className="brand"><span className="brand-mark">S</span><span>StudyRagAgent</span></div>
    <div className="side-heading"><span>我的课程</span><button className="icon-button course-add" type="button" aria-label="添加新课程" title="添加新课程" aria-haspopup="dialog" disabled={locked} onClick={() => setCreateOpen(true)}><Icon name="plus" /></button></div>
    <nav className="course-tree" aria-label="课程列表">{courseList.map(item => <div className="course-group" key={item.id}>
      <div className={`course-row${item.id === courseId ? ' current' : ''}`}>
        <button className="course" aria-expanded={expanded.has(item.id)} aria-controls={`course-chats-${item.id}`} disabled={locked} onClick={() => selectCourse(item)}><span className={`course-chevron${expanded.has(item.id) ? ' expanded' : ''}`}><Icon name="chevron" size={13} /></span><span className="course-label">{item.name}</span></button>
        <div className="course-row-tools"><button className="icon-button" aria-label={`${item.name}的课程资料`} title="课程资料" onClick={() => setLibrary(item)}><Icon name="book" size={16} /></button><button className="icon-button" aria-label={`在${item.name}中新建对话`} title="新建对话" disabled={locked || loading} onClick={() => void newSession(item)}><Icon name="plus" size={17} /></button></div>
      </div>
      {expanded.has(item.id) && <div id={`course-chats-${item.id}`} className="session-list" role="group" aria-label={`${item.name}的对话`}>
        {(sessions[item.id] ?? []).map(chat => <div className={`session-row${chat.id === sessionId && item.id === courseId ? ' selected' : ''}`} key={chat.id} onContextMenu={event => { event.preventDefault(); showMenu(chat, event.clientX, event.clientY) }}>
          <button className="text-button session-link" data-session-id={chat.id} title={chat.title || '新对话'} aria-current={chat.id === sessionId && item.id === courseId ? 'page' : undefined} disabled={locked} onClick={() => selectSession(chat)} onKeyDown={event => { if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) { event.preventDefault(); const box = event.currentTarget.getBoundingClientRect(); showMenu(chat, box.left, box.bottom) } }}>{chat.title || '新对话'}</button>
          <button className="icon-button session-more" aria-label={`${chat.title || '新对话'}的更多操作`} title="更多操作" aria-haspopup="menu" onClick={event => { const box = event.currentTarget.getBoundingClientRect(); showMenu(chat, box.left, box.bottom) }}><Icon name="more" size={16} /></button>
        </div>)}
        {!sessions[item.id]?.length && <p className="session-empty">{loading && item.id === courseId ? '正在加载…' : '点击 + 开始新对话'}</p>}
      </div>}
    </div>)}</nav>
    <div className="account"><div className="account-header"><span className="account-email">{user.email}</span><ThemeToggle /></div><button disabled={locked} onClick={onLogout}>退出</button></div>
  </>}>
    {courseId ? <Chat key={view} accountId={user.id} courseId={courseId} courseName={currentCourse?.name ?? ''} session={currentSession} loadingSession={loading || creating} docs={docs} onSaved={refreshDocuments} onSessionCreated={createdSession} onSessionUpdated={updateSession} onSessionsRefresh={() => { void loadSessions(courseId).catch(e => setError(String(e))) }} onBusyChange={setBusy} onOpenSidebar={() => setMobileSidebar(true)} /> : <div className="workspace-welcome"><button type="button" className="icon-button mobile-nav-button" aria-label="打开课程导航" onClick={() => setMobileSidebar(true)}><Icon name="menu" /></button><div className="empty welcome-panel"><h2>从第一门课程开始</h2><p>创建课程，开始整理资料和提问。</p><button type="button" onClick={() => setCreateOpen(true)}>创建第一门课程</button></div></div>}
    {error && <div className="workspace-toast alert" role="alert">{error}<button className="icon-button" aria-label="关闭错误提示" onClick={() => setError('')}><Icon name="close" size={15} /></button></div>}
    {notice && <div className="workspace-toast notice" role="status">{notice}</div>}
    {createOpen && <CreateCourseDialog onClose={() => setCreateOpen(false)} onCreated={item => { setCourseList(current => [...current, item]); setCourseId(item.id); setSessionId(''); setExpanded(current => new Set(current).add(item.id)); setView(value => value + 1); setCreateOpen(false); setMobileSidebar(false); setNotice('课程已创建') }} />}
    {library && <CourseLibrary key={library.id} course={library} onClose={() => setLibrary(null)} onChanged={libraryChanged} />}
    {rename && <RenameDialog session={rename} onClose={() => setRename(null)} onSaved={updateSession} />}
    {menu && createPortal(<div ref={menuRef} className="session-menu" role="menu" aria-label="对话操作" style={{ left: menu.x, top: menu.y }} onKeyDown={event => {
      if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button')); const index = buttons.indexOf(document.activeElement as HTMLButtonElement); buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus() }
    }}><button className="text-button" role="menuitem" onClick={() => { setRename(menu.session); setMenu(null) }}>重命名</button><button className="text-button" role="menuitem" disabled={locked} onClick={() => void removeSession(menu.session)}>删除对话</button></div>, document.body)}
  </WorkspaceLayout>
}
