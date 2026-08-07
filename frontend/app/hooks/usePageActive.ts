import { createContext, useContext } from 'react'

/**
 * Context to signal whether a keep-alive page is the currently active (visible) page.
 * Components can use `usePageActive()` to pause polling / heavy work when hidden.
 */
const PAGE_ACTIVE_DEFAULT = false
export const PageActiveContext = createContext<boolean>(PAGE_ACTIVE_DEFAULT)

/**
 * Returns `true` when the page this component belongs to is the visible/active page,
 * `false` when the page is kept alive but hidden behind another page.
 *
 * Usage:
 * ```tsx
 * const active = usePageActive()
 * useInterval(fetchData, active ? 5000 : null) // pauses when hidden
 * ```
 */
export function usePageActive(): boolean {
  return useContext(PageActiveContext)
}
