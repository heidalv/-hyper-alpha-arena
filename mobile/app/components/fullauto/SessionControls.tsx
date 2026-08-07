import React, { useState } from 'react'
import { TouchButton } from '../ui/TouchButton'
import { BottomSheet } from '../ui/BottomSheet'

interface SessionControlsProps {
  sessionStatus: 'running' | 'defensive' | 'paused' | 'stopped' | null
  onPause: () => void
  onResume: () => void
  onStop: () => void
  onStart: () => void
}

export const SessionControls: React.FC<SessionControlsProps> = ({
  sessionStatus,
  onPause,
  onResume,
  onStop,
  onStart
}) => {
  const [confirmAction, setConfirmAction] = useState<string | null>(null)
  const [pendingAction, setPendingAction] = useState<(() => void) | null>(null)

  const handleConfirm = (label: string, action: () => void) => {
    setConfirmAction(label)
    setPendingAction(() => action)
  }

  const executeAction = () => {
    pendingAction?.()
    setConfirmAction(null)
    setPendingAction(null)
  }

  if (!sessionStatus || sessionStatus === 'stopped') {
    return (
      <div className="mx-4 mt-3">
        <TouchButton variant="primary" fullWidth size="lg" onClick={onStart}>
          启动新会话
        </TouchButton>

        <BottomSheet open={!!confirmAction} onClose={() => setConfirmAction(null)} title="确认启动">
          <div className="pb-4">
            <p className="text-sm text-muted mb-4">确定要启动新的交易会话吗？</p>
            <TouchButton variant="primary" fullWidth onClick={executeAction}>
              确认启动
            </TouchButton>
          </div>
        </BottomSheet>
      </div>
    )
  }

  return (
    <div className="mx-4 mt-3 space-y-3">
      {sessionStatus === 'running' && (
        <>
          <TouchButton variant="warning" fullWidth onClick={() => handleConfirm('暂停交易', onPause)}>
            暂停交易
          </TouchButton>
          <TouchButton variant="danger" fullWidth onClick={() => handleConfirm('停止会话', onStop)}>
            停止会话
          </TouchButton>
        </>
      )}

      {sessionStatus === 'paused' && (
        <>
          <TouchButton variant="success" fullWidth onClick={onResume}>
            恢复交易
          </TouchButton>
          <TouchButton variant="danger" fullWidth onClick={() => handleConfirm('停止会话', onStop)}>
            停止会话
          </TouchButton>
        </>
      )}

      {sessionStatus === 'defensive' && (
        <TouchButton variant="danger" fullWidth onClick={() => handleConfirm('停止会话', onStop)}>
          停止会话
        </TouchButton>
      )}

      {/* Confirmation BottomSheet */}
      <BottomSheet open={!!confirmAction} onClose={() => setConfirmAction(null)} title={confirmAction ?? ''}>
        <div className="pb-4">
          <p className="text-sm text-muted mb-4">确定要执行此操作吗？</p>
          <div className="flex gap-3">
            <TouchButton variant="ghost" fullWidth onClick={() => setConfirmAction(null)}>
              取消
            </TouchButton>
            <TouchButton variant="danger" fullWidth onClick={executeAction}>
              确认
            </TouchButton>
          </div>
        </div>
      </BottomSheet>
    </div>
  )
}
