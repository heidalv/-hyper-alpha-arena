/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './app/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        terminal: {
          bg: 'rgb(var(--terminal-bg) / <alpha-value>)',
          card: 'rgb(var(--terminal-card) / <alpha-value>)',
          'card-2': 'rgb(var(--terminal-card-2) / <alpha-value>)',
          border: 'rgb(var(--terminal-border) / <alpha-value>)',
          profit: 'rgb(var(--terminal-profit) / <alpha-value>)',
          loss: 'rgb(var(--terminal-loss) / <alpha-value>)',
          primary: 'rgb(var(--terminal-primary) / <alpha-value>)',
          muted: 'rgb(var(--terminal-muted) / <alpha-value>)',
          text: 'rgb(var(--terminal-text) / <alpha-value>)',
          warning: 'rgb(var(--terminal-warning) / <alpha-value>)',
          // [2026-09-28] 模拟盘身份色（与实盘 loss 红对立，防误操作）
          paper: 'rgb(var(--terminal-paper) / <alpha-value>)',
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['SF Mono', 'Menlo', 'monospace'],
      },
      // 浅色下层次靠投影（夜间为 none），见 app/index.css 的 --terminal-shadow
      boxShadow: {
        card: 'var(--terminal-shadow)',
      },
    },
  },
  plugins: [],
}
