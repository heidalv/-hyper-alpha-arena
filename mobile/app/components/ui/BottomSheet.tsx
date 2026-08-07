import React, { useState, useRef } from 'react'

interface BottomSheetProps {
  open: boolean
  onClose: () => void
  children: React.ReactNode
  title?: string
}

function BottomSheet({ open, onClose, children, title }: BottomSheetProps) {
  if (!open) return null

  return (
    <>
      <div className="sheet-overlay" onClick={onClose} />
      <div className="sheet-content">
        <div className="sheet-handle" />
        {title && (
          <div className="px-5 pb-3 pt-1 border-b border-terminal-border">
            <h3 className="text-lg font-semibold text-terminal-text">{title}</h3>
          </div>
        )}
        <div className="p-5">{children}</div>
      </div>
    </>
  )
}

export default BottomSheet
// 具名导出别名，兼容 `import { BottomSheet }` 写法
export { BottomSheet }
