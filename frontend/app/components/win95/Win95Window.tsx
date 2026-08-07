/**
 * Win95Window — Classic Windows 95 window shell
 * Wraps the entire app with status bar
 */
import { ReactNode } from 'react'

interface Win95WindowProps {
  children: ReactNode
  statusSections?: Array<{ content: ReactNode; flex?: number }>
}

export default function Win95Window({
  children,
  statusSections,
}: Win95WindowProps) {
  return (
    <div className="w95-window">
      {/* Content */}
      <div className="w95-window-body">
        {children}
      </div>

      {/* Status Bar */}
      {statusSections && (
        <div className="w95-statusbar">
          {statusSections.map((s, i) => (
            <div key={i} className="w95-statusbar-section" style={{ flex: s.flex ?? 1 }}>
              {s.content}
            </div>
          ))}
        </div>
      )}

      <style>{`
        .w95-window {
          position: fixed;
          inset: 0 0 34px 0;
          background: hsl(var(--background));
          color: hsl(var(--foreground));
          border: 1px solid hsl(var(--border));
          display: flex;
          flex-direction: column;
          overflow: hidden;
          transition: background-color 0.3s ease, color 0.3s ease;
        }
        .w95-window-body {
          flex: 1;
          display: flex;
          flex-direction: column;
          overflow: hidden;
          min-height: 0;
        }
        .w95-statusbar {
          background: hsl(var(--card));
          border-top: 1px solid hsl(var(--border));
          padding: 3px 4px;
          font-size: 13px;
          display: flex;
          gap: 2px;
          flex-shrink: 0;
        }
        .w95-statusbar-section {
          border: 1px solid hsl(var(--border));
          padding: 0 6px;
          display: flex;
          align-items: center;
          gap: 4px;
          overflow: hidden;
          white-space: nowrap;
        }
      `}</style>
    </div>
  )
}
