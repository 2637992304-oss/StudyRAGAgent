import { useEffect, useRef, type ReactNode } from 'react'
import { Icon } from './Icons'

export function Modal({ title, onClose, locked = false, className = '', children }: { title: string; onClose: () => void; locked?: boolean; className?: string; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current!
    dialog.showModal()
    return () => dialog.close()
  }, [])
  return <dialog ref={ref} className={`course-dialog ${className}`} aria-label={title} onClose={onClose} onCancel={event => { if (locked) event.preventDefault() }} onClick={event => {
    if (locked || event.target !== event.currentTarget) return
    const box = event.currentTarget.getBoundingClientRect()
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) event.currentTarget.close()
  }}>
    <div className="course-dialog-header"><h2>{title}</h2><button type="button" className="icon-button dialog-close" aria-label={`关闭${title}`} title="关闭" disabled={locked} onClick={() => ref.current?.close()}><Icon name="close" /></button></div>
    {children}
  </dialog>
}
