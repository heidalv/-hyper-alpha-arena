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
          border: 'rgb(var(--terminal-border) / <alpha-value>)',
          profit: 'rgb(var(--terminal-profit) / <alpha-value>)',
          loss: 'rgb(var(--terminal-loss) / <alpha-value>)',
          primary: 'rgb(var(--terminal-primary) / <alpha-value>)',
          muted: 'rgb(var(--terminal-muted) / <alpha-value>)',
          text: 'rgb(var(--terminal-text) / <alpha-value>)',
          warning: 'rgb(var(--terminal-warning) / <alpha-value>)',
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['SF Mono', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
