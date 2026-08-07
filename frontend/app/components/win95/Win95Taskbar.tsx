/**
 * Win95Taskbar — Bottom taskbar with Start button, page label, and tray clock
 */
import { useState, useEffect } from 'react'

const PAGE_LABELS: Record<string, string> = {
  comprehensive: '📊 仪表盘',
  klines: '📈 K线图表',
  'atas-v2': '🤖 AI决策中心',
  'modern-signals': '📡 信号系统',
  strategy: '📋 策略管理',
  risk: '🛡️ 风控监控',
  settings: '⚙ 设置',
  'data-center': '🗄️ 数据中心',
  'system-logs': '📜 系统日志',
  'trader-management': '🧠 AI交易员',
  'user-guide': '📖 使用指南',
  'prompt-management': '📝 提示词',
  attribution: '📊 归因分析',
  analytics: '📊 数据分析',
  'smart-signal-generator': '✨ 信号生成器',
  'unified-factor': '🔬 因子系统',
  'market-scanner': '🔍 市场扫描器',
  'exchange-hub': '🔄 交易所枢纽',
  'data-quality': '📡 数据质量',
  'hypothesis': '💡 策略假设',
  'fee-monitor': '💰 费率监控',
  'exchange-config': '🔑 交易所配置',
  'ai-learning-center': '🎓 AI学习中心',
  'opencode-center': '🧠 OpenCode',
}

interface Win95TaskbarProps {
  currentPage: string
}

export default function Win95Taskbar({ currentPage }: Win95TaskbarProps) {
  const [clock, setClock] = useState('')

  useEffect(() => {
    const tick = () => {
      const now = new Date()
      setClock(now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))
    }
    tick()
    const id = setInterval(tick, 10000)
    return () => clearInterval(id)
  }, [])

  const label = PAGE_LABELS[currentPage] || '📊 Alpha Arena'

  return (
    <div className="w95-taskbar">
      <div className="w95-start-btn">
        <div className="w95-start-logo" />
        <span style={{ fontWeight: 'bold' }}>Start</span>
      </div>
      <div className="w95-taskbar-divider" />
      <button className="w95-task-btn active">
        {label}
      </button>
      <div className="w95-tray">
        <span className="w95-tray-led" />
        <span>{clock}</span>
      </div>

      <style>{`
        .w95-taskbar {
          position: fixed;
          bottom: 0;
          left: 0;
          right: 0;
          height: 34px;
          background: hsl(var(--card));
          border-top: 1px solid hsl(var(--border));
          display: flex;
          align-items: center;
          padding: 2px 4px;
          gap: 3px;
          z-index: 9999;
          user-select: none;
          color: hsl(var(--foreground));
          transition: background-color 0.3s ease, color 0.3s ease;
        }
        .w95-start-btn {
          background: hsl(var(--background));
          border: 1px solid hsl(var(--border));
          padding: 2px 8px;
          font-size: 13px;
          cursor: pointer;
          display: flex;
          align-items: center;
          gap: 4px;
          height: 28px;
          color: hsl(var(--foreground));
        }
        .w95-start-btn:active {
          background: hsl(var(--muted));
        }
        .w95-start-logo {
          width: 16px;
          height: 16px;
          background: linear-gradient(135deg, #FF0000 25%, #00FF00 25%, #00FF00 50%, #0000FF 50%, #0000FF 75%, #FFFF00 75%);
          image-rendering: pixelated;
        }
        .w95-taskbar-divider {
          width: 1px;
          height: 20px;
          background: hsl(var(--border));
          margin: 0 2px;
          border-right: none;
        }
        .w95-task-btn {
          background: hsl(var(--background)) !important;
          border: 1px solid hsl(var(--border)) !important;
          padding: 2px 10px !important;
          font-size: 13px !important;
          cursor: pointer;
          height: 28px !important;
          min-height: 28px !important;
          min-width: 120px !important;
          text-align: left !important;
          color: hsl(var(--foreground)) !important;
        }
        .w95-task-btn.active {
          background: hsl(var(--muted)) !important;
          border: 1px solid hsl(var(--border)) !important;
        }
        .w95-tray {
          margin-left: auto;
          border: 1px solid hsl(var(--border));
          padding: 0 10px;
          height: 28px;
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 13px;
        }
        .w95-tray-led {
          width: 8px;
          height: 8px;
          border-radius: 50% !important;
          background: #00FF00;
          border: 1px solid #008000;
        }
      `}</style>
    </div>
  )
}
