import React from 'react'

interface TouchButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'danger' | 'success' | 'ghost' | 'warning'
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
  fullWidth?: boolean
  children: React.ReactNode
}

function TouchButton({
  variant = 'primary',
  size = 'md',
  loading = false,
  fullWidth = false,
  children,
  disabled,
  className = '',
  ...rest
}: TouchButtonProps) {
  const base = {
    primary: 'btn-primary',
    danger: 'btn-danger',
    success: 'btn-success',
    warning: 'btn-warning',
    ghost: 'btn-ghost',
  }[variant]

  const sizeClass = size === 'lg' ? 'min-h-[48px] text-base' : size === 'sm' ? 'min-h-[36px] text-xs' : 'min-h-[44px] text-sm'

  return (
    <button
      className={`${base} ${sizeClass} ${fullWidth ? 'w-full' : ''} ${className}`}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? (
        <span className="flex items-center justify-center gap-2">
          <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
          处理中...
        </span>
      ) : children}
    </button>
  )
}

export default TouchButton
// 具名导出别名，兼容 `import { TouchButton }` 写法
export { TouchButton }

