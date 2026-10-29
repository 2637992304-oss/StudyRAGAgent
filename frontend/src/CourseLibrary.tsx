import { useCallback, useEffect, useRef, useState } from 'react'
import { documents, job, openDocument, remove, retry, upload, type Course, type Document, type Job } from './api'
import { Icon } from './Icons'
import { Modal } from './Modal'

const types = ['课件', '笔记', '试卷', '作业习题', '教材', '参考资料']
const stages: Record<string, string> = { ready: '可使用', queued: '等待处理', processing: '正在处理', parsing: '正在解析', embedding: '正在生成索引', completed: '可使用', failed: '处理失败', queue: '队列不可用' }

export function CourseLibrary({ course, onClose, onChanged }: { course: Course; onClose: () => void; onChanged: (id: string) => void }) {
  const [docs, setDocs] = useState<Document[]>([])
  const [jobs, setJobs] = useState<Record<string, Job>>({})
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [type, setType] = useState(types[0])
  const [progress, setProgress] = useState<number | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const alive = useRef(true)
  const working = useRef(false)
  const refresh = useCallback(async () => {
    const list = await documents(course.id)
    const pairs = await Promise.all(list.filter(item => item.job_id).map(async item => {
      try { return [item.job_id, await job(item.job_id)] as const } catch { return null }
    }))
    if (!alive.current) return
    setDocs(list)
    setJobs(Object.fromEntries(pairs.filter(pair => pair !== null)))
  }, [course.id])
  useEffect(() => {
    alive.current = true
    refresh().catch(e => { if (alive.current) setError(String(e)) }).finally(() => { if (alive.current) setLoading(false) })
    return () => { alive.current = false }
  }, [refresh])
  useEffect(() => {
    if (!docs.some(item => ['queued', 'processing'].includes(item.status))) return
    const interval = window.setInterval(() => { void refresh().then(() => onChanged(course.id)).catch(() => undefined) }, 2000)
    return () => window.clearInterval(interval)
  }, [docs, refresh, course.id, onChanged])
  async function perform(action: () => Promise<void>) {
    if (working.current) return
    working.current = true; setBusy(true); setError('')
    try { await action(); await refresh(); onChanged(course.id) }
    catch (e) { if (alive.current) setError(e instanceof Error ? e.message : String(e)) }
    finally { working.current = false; if (alive.current) { setBusy(false); setProgress(null) } }
  }
  return <Modal title="课程资料" className="library-dialog" onClose={onClose} locked={busy}>
    <div className="library-toolbar"><p className="library-course">{course.name}<span>{docs.length} 份资料</span></p><div className="library-tools">
      <button type="button" className="text-button" disabled={busy} onClick={() => { setError(''); void refresh().catch(e => setError(String(e))) }}>刷新状态</button>
      <select aria-label="资料类型" value={type} disabled={busy} onChange={event => setType(event.target.value)}>{types.map(value => <option key={value}>{value}</option>)}</select>
      <input className="composer-file-input" ref={fileRef} type="file" aria-label="选择课程资料" accept=".pdf,.docx,.pptx,.txt,.md,.markdown,.jpg,.jpeg,.png,.webp" disabled={busy} onChange={event => {
        const file = event.target.files?.[0]; event.target.value = ''
        if (file) void perform(() => upload(course.id, file, type, setProgress))
      }} />
      <button type="button" className="icon-button library-upload" aria-label="上传课程资料" title="上传资料" disabled={busy} onClick={() => fileRef.current?.click()}><Icon name="plus" size={21} /></button>
    </div></div>
    {error && <p className="alert" role="alert">{error}</p>}
    {progress !== null && <div className="upload-progress" role="status"><span>上传中 {progress}%</span><progress max={100} value={progress} /></div>}
    <div className="library-documents" aria-busy={loading}>
      {loading ? <p className="empty">正在加载资料…</p> : docs.length === 0 ? <p className="empty">还没有资料，点击右上角的 + 上传第一份笔记或课件。</p> : docs.map(item => <article className="document" key={item.id}>
        <div className="file-icon">{item.filename.split('.').pop()?.toUpperCase()}</div>
        <div className="document-details"><button className="text-button document-name" onClick={() => openDocument(item.id, null).catch(e => setError(String(e)))}>{item.filename}</button><p>{item.document_type} · {(item.size_bytes / 1024).toFixed(1)} KB</p>{item.parse_quality === 'partial' && <p className="job-error">资料已可检索，但部分内容可能未完整提取。</p>}{item.parse_warnings?.map((warning, index) => <p className="job-error" key={index}>{warning}</p>)}{jobs[item.job_id]?.error && <p className="job-error">{jobs[item.job_id].error}</p>}</div>
        <span className={`badge ${item.status}`}>{stages[jobs[item.job_id]?.stage ?? item.status] ?? item.status}</span>
        <div className="actions">{item.status === 'failed' && <button disabled={busy} onClick={() => void perform(() => retry(item.id))}>重试</button>}<button className="danger" disabled={busy} onClick={() => { if (window.confirm(`删除「${item.filename}」及其索引？`)) void perform(() => remove(item.id)) }}>删除</button></div>
      </article>)}
    </div>
  </Modal>
}
