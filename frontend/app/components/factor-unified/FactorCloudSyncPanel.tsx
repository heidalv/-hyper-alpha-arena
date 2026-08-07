/**
 * FactorCloudSyncPanel — 云端因子库同步管理
 *
 * 管理云端因子库同步配置，触发同步，查看同步状态。
 * 对接 /api/factors/sync/* 和 /api/factors/cloud/*
 */
import { useState, useEffect, useCallback }from 'react'
import { apiRequest } from '@/lib/api'

interface SyncConfig {
  id: number
  name: string
  repo_url: string
  branch: string
  enabled: boolean
  auto_sync: boolean
  sync_interval_hours: number
  last_sync_at: string | null
  last_sync_status: string | null
  factors_downloaded: number
  factors_registered: number
}

export default function FactorCloudSyncPanel() {
  const [configs, setConfigs] = useState<SyncConfig[]>([])
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState<any>(null)
  const [showAdd, setShowAdd] = useState(false)

  // 新建表单
  const [newName, setNewName] = useState('')
  const [newUrl, setNewUrl] = useState('')
  const [newBranch, setNewBranch] = useState('main')
  const [newPath, setNewPath] = useState('')
  const [newAuto, setNewAuto] = useState(false)

  const fetchConfigs = useCallback(async () => {
    try {
      const resp = await apiRequest('/factors/sync/configs')
      const data = await resp.json()
      setConfigs(data.configs || [])
    } catch {
      setConfigs([])
    }
  }, [])

  useEffect(() => {
    fetchConfigs()
  }, [fetchConfigs])

  const runSync = useCallback(async (configId?: number) => {
    setSyncing(true)
    setSyncResult(null)
    try {
      const url = configId
        ? `/factors/sync/run?config_id=${configId}`
        : '/factors/sync/run'
      const resp = await apiRequest(url, { method: 'POST' })
      const data = await resp.json()
      setSyncResult(data)
      fetchConfigs()
    } catch (e: any) {
      setSyncResult({ status: 'error', reason: e.message || '同步失败' })
    } finally {
      setSyncing(false)
    }
  }, [fetchConfigs])

  const createConfig = useCallback(async () => {
    if (!newName || !newUrl) return
    try {
      await apiRequest('/factors/sync/configs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newName,
          repo_url: newUrl,
          branch: newBranch,
          sync_path: newPath || null,
          auto_sync: newAuto,
        }),
      })
      setShowAdd(false)
      setNewName('')
      setNewUrl('')
      setNewBranch('main')
      setNewPath('')
      setNewAuto(false)
      fetchConfigs()
    } catch (e: any) {
      alert('创建失败: ' + (e.message || '未知错误'))
    }
  }, [newName, newUrl, newBranch, newPath, newAuto, fetchConfigs])

  const deleteConfig = useCallback(async (id: number) => {
    if (!confirm('确定删除此同步配置？')) return
    try {
      await apiRequest(`/factors/sync/configs/${id}`, { method: 'DELETE' })
      fetchConfigs()
    } catch {
      // ignore
    }
  }, [fetchConfigs])

  const toggleEnabled = useCallback(async (id: number, enabled: boolean) => {
    try {
      await apiRequest(`/factors/sync/configs/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      fetchConfigs()
    } catch {
      // ignore
    }
  }, [fetchConfigs])

  return (
    <div className="space-y-4">
      {/* 操作栏 */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">同步配置</h3>
        <div className="flex gap-2">
          <button
            onClick={() => runSync()}
            disabled={syncing}
            className="px-3 py-1.5 rounded bg-blue-600 text-white text-xs hover:bg-blue-700 disabled:opacity-50"
          >
            {syncing ? '同步中...' : '同步全部'}
          </button>
          <button
            onClick={() => setShowAdd(true)}
            className="px-3 py-1.5 rounded bg-muted text-xs hover:bg-muted/80"
          >
            + 新建配置
          </button>
        </div>
      </div>

      {/* 同步结果 */}
      {syncResult && (
        <div className={`rounded-lg border p-3 text-xs ${
          syncResult.status === 'completed'
            ? 'bg-green-500/10 border-green-500/30'
            : 'bg-red-500/10 border-red-500/30'
        }`}>
          <div className="font-medium mb-1">
            {syncResult.status === 'completed' ? '同步完成' : '同步失败'}
          </div>
          {syncResult.status === 'completed' && (
            <div className="text-muted-foreground">
              下载 {syncResult.downloaded} 个 · 本地化 {syncResult.localized} 个 · 失败 {syncResult.errors} 个
            </div>
          )}
          {syncResult.reason && (
            <div className="text-red-400">{syncResult.reason}</div>
          )}
        </div>
      )}

      {/* 新建配置表单 */}
      {showAdd && (
        <div className="rounded-lg border bg-card p-4 space-y-3">
          <h4 className="text-xs font-medium">新建同步配置</h4>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] text-muted-foreground">名称</label>
              <input
                value={newName}
                onChange={e => setNewName(e.target.value)}
                className="w-full h-8 rounded border bg-background px-2 text-xs mt-0.5"
                placeholder="我的因子库"
              />
            </div>
            <div>
              <label className="text-[10px] text-muted-foreground">仓库 URL</label>
              <input
                value={newUrl}
                onChange={e => setNewUrl(e.target.value)}
                className="w-full h-8 rounded border bg-background px-2 text-xs mt-0.5"
                placeholder="https://github.com/..."
              />
            </div>
            <div>
              <label className="text-[10px] text-muted-foreground">分支</label>
              <input
                value={newBranch}
                onChange={e => setNewBranch(e.target.value)}
                className="w-full h-8 rounded border bg-background px-2 text-xs mt-0.5"
              />
            </div>
            <div>
              <label className="text-[10px] text-muted-foreground">因子目录路径 (可选)</label>
              <input
                value={newPath}
                onChange={e => setNewPath(e.target.value)}
                className="w-full h-8 rounded border bg-background px-2 text-xs mt-0.5"
                placeholder="factors/"
              />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={newAuto}
              onChange={e => setNewAuto(e.target.checked)}
              className="rounded"
            />
            <span className="text-xs">自动同步</span>
          </div>
          <div className="flex gap-2">
            <button
              onClick={createConfig}
              className="px-3 py-1.5 rounded bg-blue-600 text-white text-xs"
            >
              创建
            </button>
            <button
              onClick={() => setShowAdd(false)}
              className="px-3 py-1.5 rounded bg-muted text-xs"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {/* 配置列表 */}
      {configs.length === 0 ? (
        <div className="text-center py-12 text-sm text-muted-foreground">
          暂无同步配置，点击"新建配置"添加
        </div>
      ) : (
        <div className="space-y-3">
          {configs.map(cfg => (
            <div key={cfg.id} className="rounded-lg border bg-card p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{cfg.name}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                    cfg.last_sync_status === 'success'
                      ? 'bg-green-500/20 text-green-400'
                      : cfg.last_sync_status === 'failed'
                        ? 'bg-red-500/20 text-red-400'
                        : 'bg-muted text-muted-foreground'
                  }`}>
                    {cfg.last_sync_status || '未同步'}
                  </span>
                  {cfg.auto_sync && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400">
                      自动
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => toggleEnabled(cfg.id, !cfg.enabled)}
                    className={`text-xs px-2 py-0.5 rounded ${
                      cfg.enabled ? 'bg-green-500/20 text-green-400' : 'bg-muted text-muted-foreground'
                    }`}
                  >
                    {cfg.enabled ? '启用' : '禁用'}
                  </button>
                  <button
                    onClick={() => runSync(cfg.id)}
                    disabled={syncing}
                    className="text-xs px-2 py-0.5 rounded bg-blue-600 text-white disabled:opacity-50"
                  >
                    同步
                  </button>
                  <button
                    onClick={() => deleteConfig(cfg.id)}
                    className="text-xs px-2 py-0.5 rounded bg-red-500/20 text-red-400"
                  >
                    删除
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-4 gap-2 text-[10px] text-muted-foreground">
                <div>仓库: {cfg.repo_url}</div>
                <div>分支: {cfg.branch}</div>
                <div>已下载: {cfg.factors_downloaded}</div>
                <div>已注册: {cfg.factors_registered}</div>
              </div>
              {cfg.last_sync_at && (
                <div className="text-[10px] text-muted-foreground mt-1">
                  上次同步: {cfg.last_sync_at}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
