/** 在 React 挂载前应用主题，避免闪烁 */
export type ThemePreference = 'light' | 'dark' | 'system'

export const THEME_STORAGE_KEY = 'hyper-alpha-arena-theme'

export function resolveTheme(preference: ThemePreference): 'light' | 'dark' {
  if (preference === 'dark') return 'dark'
  if (preference === 'light') return 'light'
  if (typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches) {
    return 'dark'
  }
  return 'light'
}

export function applyThemeClass(preference: ThemePreference): 'light' | 'dark' {
  const effective = resolveTheme(preference)
  const root = document.documentElement
  root.classList.remove('light', 'dark')
  root.classList.add(effective)
  return effective
}

export function readStoredTheme(): ThemePreference {
  if (typeof window === 'undefined') return 'light'
  const stored = localStorage.getItem(THEME_STORAGE_KEY)
  if (stored === 'dark' || stored === 'light' || stored === 'system') {
    return stored
  }
  return 'light'
}
