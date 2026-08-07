import { lazy, type ComponentType } from 'react'

function isChunkLoadError(error: unknown): boolean {
  const msg = error instanceof Error ? error.message : String(error)
  return (
    msg.includes('Failed to fetch dynamically imported module') ||
    msg.includes('Importing a module script failed') ||
    msg.includes('error loading dynamically imported module')
  )
}

/**
 * 带 stale-chunk 自动刷新的懒加载包装器（应对 Vite HMR 后模块 404）
 */
export function lazyLoad(
  importFn: () => Promise<{ default: ComponentType<any> }>,
  componentName: string
) {
  return lazy(async () => {
    try {
      return await importFn()
    } catch (error) {
      if (isChunkLoadError(error)) {
        const reloadKey = `vite-chunk-reload:${componentName}`
        if (!sessionStorage.getItem(reloadKey)) {
          sessionStorage.setItem(reloadKey, '1')
          window.location.reload()
          return new Promise(() => undefined as never)
        }
        sessionStorage.removeItem(reloadKey)
      }

      console.error(`Failed to load ${componentName}:`, error)
      return {
        default: () => (
          <div className="flex items-center justify-center min-h-[400px]">
            <div className="text-center space-y-3">
              <p className="text-red-500">无法加载 {componentName}</p>
              <p className="text-sm text-muted-foreground">
                开发模式下 Vite 热更新可能导致模块过期，请刷新页面
              </p>
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="px-4 py-2 text-sm rounded-md bg-secondary hover:bg-secondary/80"
              >
                刷新页面
              </button>
            </div>
          </div>
        ),
      }
    }
  })
}
