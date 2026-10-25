import { useEffect, useState } from 'react'
import { Icon } from './Icons'
import { applyTheme, readTheme, THEME_KEY } from './theme'

export function ThemeToggle() {
  const [theme, setTheme] = useState(readTheme)
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== THEME_KEY && event.key !== null) return
      const next = readTheme()
      document.documentElement.dataset.theme = next
      document.documentElement.style.colorScheme = next
      setTheme(next)
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])
  const label = theme === 'light' ? '切换到深色模式' : '切换到浅色模式'
  return <button type="button" className="icon-button theme-toggle" aria-label={label} title={label} aria-pressed={theme === 'dark'} onClick={() => {
    const next = theme === 'light' ? 'dark' : 'light'
    applyTheme(next)
    setTheme(next)
  }}><Icon name={theme === 'light' ? 'moon' : 'sun'} size={19} /></button>
}
