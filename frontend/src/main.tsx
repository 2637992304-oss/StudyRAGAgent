import { useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { courses, login, me, register, setToken, type Course, type User } from './api'
import './style.css'
import { StudyWorkspace } from './StudyWorkspace'
import { applyTheme, readTheme } from './theme'
import './theme.css'

applyTheme(readTheme())

function App() {
  const [user, setUser] = useState<User | null>(null)
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const authSubmitting = useRef(false)
  const [checkingSession, setCheckingSession] = useState(true)
  const [courseList, setCourseList] = useState<Course[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const refreshCourses = useCallback(async () => {
    const list = await courses()
    setCourseList(list)
  }, [])

  useEffect(() => {
    me().then(async current => { await refreshCourses(); setUser(current) })
      .catch(() => setToken(''))
      .finally(() => setCheckingSession(false))
  }, [refreshCourses])

  async function submitAuth() {
    if (checkingSession || authSubmitting.current) return
    setError(''); setNotice('')
    if (authMode === 'register' && password !== confirmPassword) {
      setError('两次输入的密码不一致')
      return
    }
    if (authMode === 'register' && !password.trim()) {
      setError('密码不能全部为空格')
      return
    }
    authSubmitting.current = true
    setBusy(true)
    try {
      await (authMode === 'register' ? register : login)(email.trim(), password)
      const current = await me()
      await refreshCourses()
      setUser(current)
      setPassword(''); setConfirmPassword('')
      setNotice(authMode === 'register' ? '注册成功，欢迎来到你的学习空间' : '登录成功')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      authSubmitting.current = false
      setBusy(false)
    }
  }

  function switchAuthMode(mode: 'login' | 'register') {
    setAuthMode(mode)
    setPassword(''); setConfirmPassword(''); setError(''); setNotice('')
  }

  function logout() {
    setToken(''); setUser(null); setCourseList([])
    setPassword(''); setConfirmPassword(''); setError(''); setNotice(''); setAuthMode('login')
  }

  if (!user) return (
    <main className="login-shell">
      <div className="brand"><span className="brand-mark">S</span><span>StudyRagAgent</span></div>
      <section className="login-card">
        <p className="eyebrow">个人学习空间</p>
        <h1>让自己的资料，<br />成为学习的起点。</h1>
        <p className="muted">{authMode === 'register' ? '创建账号，开始整理自己的课程与资料。' : '登录后整理课程与资料。每个账号的内容仅自己可见。'}</p>
        <div className="auth-modes" role="group" aria-label="账号操作">
          <button type="button" className="text-button" aria-pressed={authMode === 'login'} disabled={busy || checkingSession} onClick={() => switchAuthMode('login')}>登录</button>
          <button type="button" className="text-button" aria-pressed={authMode === 'register'} disabled={busy || checkingSession} onClick={() => switchAuthMode('register')}>注册</button>
        </div>
        <form onSubmit={event => { event.preventDefault(); void submitAuth() }}>
          <label>邮箱<input name="email" type="email" autoComplete="username" value={email} maxLength={255} disabled={busy || checkingSession} onChange={event => setEmail(event.target.value)} required /></label>
          <label>密码<input name="password" type="password" autoComplete={authMode === 'register' ? 'new-password' : 'current-password'} value={password} minLength={authMode === 'register' ? 15 : undefined} maxLength={authMode === 'register' ? 128 : undefined} aria-describedby={authMode === 'register' ? 'password-hint' : undefined} disabled={busy || checkingSession} onChange={event => setPassword(event.target.value)} required /></label>
          {authMode === 'register' && <>
            <p id="password-hint" className="password-hint">密码需为 15–128 个字符，可使用空格和符号。</p>
            <label>确认密码<input name="confirmPassword" type="password" autoComplete="new-password" value={confirmPassword} minLength={15} maxLength={128} disabled={busy} onChange={event => setConfirmPassword(event.target.value)} required /></label>
          </>}
          <button disabled={busy || checkingSession} type="submit">{checkingSession ? '检查登录状态…' : busy ? (authMode === 'register' ? '注册中…' : '登录中…') : (authMode === 'register' ? '注册并进入学习空间' : '进入学习空间')}</button>
        </form>
        {error && <p className="alert" role="alert">{error}</p>}
        <p className="hint">{authMode === 'register' ? '当前为本地使用，注册后即可进入，无需邮箱验证。' : '还没有账号？点击上方「注册」创建个人学习空间。'}</p>
      </section>
    </main>
  )

  return <StudyWorkspace key={user.id} user={user} initialCourses={courseList} initialNotice={notice} onLogout={logout} />
}

createRoot(document.getElementById('root')!).render(<App />)
