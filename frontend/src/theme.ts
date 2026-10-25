export type Theme = 'light' | 'dark'
export const THEME_KEY = 'studyrag-theme'

export function readTheme(): Theme {
  try { return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light' }
  catch { return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light' }
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme
  try { localStorage.setItem(THEME_KEY, theme) }
  catch { /* Switching still works when browser storage is unavailable. */ }
}
