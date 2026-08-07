import React from 'react'

interface EventEntry {
  type: string
  message: string
  timestamp: string
}

interface EventLogProps {
  events: EventEntry[]
}

function formatTime(timestamp: string): string {
  const date = new Date(timestamp)
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

const typeBorderColors: Record<string, string> = {
  circuit_breaker: 'border-l-loss',
  defensive_exit: 'border-l-profit',
  strategy_created: 'border-l-primary',
  strategy_frozen: 'border-l-primary',
}

export const EventLog: React.FC<EventLogProps> = ({ events }) => {
  if (events.length === 0) {
    return (
      <div className="mx-4 mt-3 p-6 bg-surface rounded-card border border-border text-center text-muted text-sm">
        暂无事件
      </div>
    )
  }

  return (
    <div className="mx-4 mt-3">
      <h3 className="text-sm text-muted mb-2">事件日志</h3>
      <div className="bg-surface rounded-card border border-border overflow-hidden">
        {events.slice(0, 20).map((event, i) => {
          const borderColor = typeBorderColors[event.type] || 'border-l-muted'
          return (
            <div
              key={`${event.timestamp}-${i}`}
              className={`flex items-start gap-3 px-4 py-3 border-l-4 ${borderColor} ${
                i < events.length - 1 ? 'border-b border-border' : ''
              }`}
            >
              <span className="text-xs text-muted tabular-nums flex-shrink-0 mt-0.5">
                {formatTime(event.timestamp)}
              </span>
              <span className="text-sm flex-1">{event.message}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
