import React from 'react';

interface ErrorBoundaryProps {
  fallback?: React.ReactNode;
  children: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('[ErrorBoundary] Caught error:', error, errorInfo);
  }

  handleReset = () => {
    const msg = this.state.error?.message ?? ''
    const isChunkError =
      msg.includes('Failed to fetch dynamically imported module') ||
      msg.includes('Importing a module script failed') ||
      msg.includes('error loading dynamically imported module')

    if (isChunkError) {
      window.location.reload()
      return
    }
    this.setState({ hasError: false, error: null })
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      const msg = this.state.error?.message ?? ''
      const isChunkError =
        msg.includes('Failed to fetch dynamically imported module') ||
        msg.includes('Importing a module script failed') ||
        msg.includes('error loading dynamically imported module')

      return (
        <div className="flex flex-col items-center justify-center p-8 gap-4 rounded-lg border border-red-200 dark:border-red-800 bg-red-50/50 dark:bg-red-950/20">
          <div className="text-red-500 dark:text-red-400">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-10 w-10"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
          </div>
          <div className="text-center space-y-1">
            <h3 className="text-sm font-semibold text-red-700 dark:text-red-300">
              组件渲染出错
            </h3>
            <p className="text-xs text-muted-foreground max-w-sm">
              {msg || '发生了未知错误'}
            </p>
            {isChunkError && (
              <p className="text-xs text-muted-foreground max-w-sm">
                开发模式下页面热更新可能导致模块过期，请刷新页面
              </p>
            )}
          </div>
          <button
            onClick={this.handleReset}
            className="px-4 py-2 text-xs font-medium rounded-md bg-red-100 hover:bg-red-200 dark:bg-red-900/40 dark:hover:bg-red-900/60 text-red-700 dark:text-red-300 transition-colors"
          >
            {isChunkError ? '刷新页面' : '重新加载'}
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
