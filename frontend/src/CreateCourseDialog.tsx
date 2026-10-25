import { useEffect, useRef, useState } from 'react'
import { createCourse, type Course } from './api'

export function CreateCourseDialog({ onCreated, onClose }: { onCreated: (course: Course) => void; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const submittingRef = useRef(false)
  const [name, setName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const dialog = dialogRef.current!
    dialog.showModal()
    inputRef.current?.focus()
    return () => dialog.close()
  }, [])

  async function submit() {
    const trimmed = name.trim()
    if (!trimmed || submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    setError('')
    try {
      const course = await createCourse(trimmed)
      onCreated(course)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  return (
    <dialog ref={dialogRef} className="course-dialog" aria-labelledby="create-course-title" onClose={onClose} onCancel={event => { if (submittingRef.current) event.preventDefault() }} onClick={event => {
      if (event.target !== event.currentTarget || submittingRef.current) return
      const bounds = event.currentTarget.getBoundingClientRect()
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) event.currentTarget.close()
    }}>
      <div className="course-dialog-header">
        <h2 id="create-course-title">创建课程</h2>
        <button type="button" className="icon-button dialog-close" aria-label="关闭创建课程" title="关闭" disabled={submitting} onClick={() => dialogRef.current?.close()}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" /></svg>
        </button>
      </div>
      <form onSubmit={event => { event.preventDefault(); void submit() }} aria-busy={submitting}>
        <label className="course-name-field" htmlFor="new-course-name">新课程名称
          <input ref={inputRef} id="new-course-name" name="courseName" value={name} onChange={event => setName(event.target.value)} required maxLength={120} autoComplete="off" disabled={submitting} aria-invalid={error ? true : undefined} aria-describedby={error ? 'create-course-error' : undefined} />
        </label>
        {error && <p id="create-course-error" className="alert" role="alert">{error}</p>}
        <div className="course-dialog-footer"><button className="course-create" type="submit" disabled={submitting || !name.trim()}>{submitting ? '创建中…' : '创建'}</button></div>
      </form>
    </dialog>
  )
}
