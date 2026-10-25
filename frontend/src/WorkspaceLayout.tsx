import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode, type CSSProperties } from 'react'

const DEFAULT_WIDTH = 256
const MAX_WIDTH = 480
const MIN_WORKSPACE_WIDTH = 480
const DIVIDER_WIDTH = 8
const WIDTH_KEY = 'studyrag-sidebar-width'

function readWidth() {
  try {
    const saved = Number(localStorage.getItem(WIDTH_KEY))
    if (Number.isFinite(saved) && saved >= DEFAULT_WIDTH && saved <= MAX_WIDTH) return saved
  } catch { /* Keep the default layout when browser storage is unavailable. */ }
  return DEFAULT_WIDTH
}

export function WorkspaceLayout({ sidebar, children, mobileSidebarOpen = false, onCloseSidebar }: { sidebar: ReactNode; children: ReactNode; mobileSidebarOpen?: boolean; onCloseSidebar?: () => void }) {
  const [preferredWidth, setPreferredWidth] = useState(readWidth)
  const [viewportWidth, setViewportWidth] = useState(window.innerWidth)
  const [resizing, setResizing] = useState(false)
  const drag = useRef<{ pointerId: number; x: number; width: number } | null>(null)
  const maxWidth = Math.max(DEFAULT_WIDTH, Math.min(MAX_WIDTH, viewportWidth - MIN_WORKSPACE_WIDTH - DIVIDER_WIDTH))
  const width = Math.min(preferredWidth, maxWidth)

  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    if (resizing) return
    try { localStorage.setItem(WIDTH_KEY, String(preferredWidth)) }
    catch { /* Resizing still works when browser storage is unavailable. */ }
  }, [preferredWidth, resizing])

  function updateWidth(next: number) {
    setPreferredWidth(Math.round(Math.max(DEFAULT_WIDTH, Math.min(maxWidth, next))))
  }

  function startDrag(event: PointerEvent<HTMLDivElement>) {
    if (!event.isPrimary || event.button !== 0) return
    event.preventDefault()
    event.currentTarget.focus()
    event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = { pointerId: event.pointerId, x: event.clientX, width }
    setResizing(true)
  }

  function moveDrag(event: PointerEvent<HTMLDivElement>) {
    if (drag.current?.pointerId !== event.pointerId) return
    updateWidth(drag.current.width + event.clientX - drag.current.x)
  }

  function endDrag(event: PointerEvent<HTMLDivElement>) {
    if (drag.current?.pointerId !== event.pointerId) return
    drag.current = null
    setResizing(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const step = event.shiftKey ? 32 : 16
    const next = { ArrowLeft: width - step, ArrowRight: width + step, Home: DEFAULT_WIDTH, End: maxWidth }[event.key]
    if (next === undefined) return
    event.preventDefault()
    updateWidth(next)
  }

  return (
    <div className={`app-shell${resizing ? ' is-resizing' : ''}${mobileSidebarOpen ? ' sidebar-open' : ''}`} style={{ '--sidebar-width': `${width}px` } as CSSProperties}>
      {mobileSidebarOpen && <button type="button" className="sidebar-scrim text-button" aria-label="关闭课程导航" onClick={onCloseSidebar} />}
      <aside id="course-sidebar" className="sidebar" aria-label="课程侧边栏">{sidebar}</aside>
      <div
        className="pane-divider"
        role="separator"
        tabIndex={0}
        aria-label="调整课程侧边栏宽度"
        aria-orientation="vertical"
        aria-controls="course-sidebar"
        aria-valuemin={DEFAULT_WIDTH}
        aria-valuemax={maxWidth}
        aria-valuenow={width}
        aria-valuetext={`${width} 像素`}
        title="左右拖动调整宽度，双击恢复默认；也可使用左右方向键"
        onPointerDown={startDrag}
        onPointerMove={moveDrag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onLostPointerCapture={endDrag}
        onDoubleClick={() => setPreferredWidth(DEFAULT_WIDTH)}
        onKeyDown={onKeyDown}
      />
      <main className="workspace" aria-label="学习空间"><div className="workspace-content">{children}</div></main>
    </div>
  )
}
