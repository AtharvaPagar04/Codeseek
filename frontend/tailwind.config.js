/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Base backgrounds
        base: '#0d0f11',
        surface: '#111418',
        'surface-2': '#161b22',
        'surface-3': '#1c2128',
        border: '#21262d',
        'border-subtle': '#1c2128',

        // Text
        'text-primary': '#e6edf3',
        'text-secondary': '#8b949e',
        'text-muted': '#484f58',

        // Accent — electric cyan
        accent: '#00d4ff',
        'accent-dim': '#00a8cc',
        'accent-glow': 'rgba(0, 212, 255, 0.12)',

        // Status
        online: '#3fb950',
        offline: '#f85149',
        warning: '#f5a623',

        // User message
        'user-bg': '#1a2332',
        'user-border': '#264466',
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', '"Fira Code"', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.65rem', { lineHeight: '1rem' }],
      },
      keyframes: {
        blink: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0' },
        },
        fadeIn: {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        slideIn: {
          from: { transform: 'translateX(-100%)' },
          to: { transform: 'translateX(0)' },
        },
        pulse: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.3' },
        },
        dotBounce: {
          '0%, 80%, 100%': { transform: 'translateY(0)' },
          '40%': { transform: 'translateY(-6px)' },
        },
      },
      animation: {
        blink: 'blink 1s step-end infinite',
        fadeIn: 'fadeIn 0.15s ease-out',
        slideIn: 'slideIn 0.2s ease-out',
        'dot-1': 'dotBounce 1.2s ease-in-out infinite',
        'dot-2': 'dotBounce 1.2s ease-in-out 0.2s infinite',
        'dot-3': 'dotBounce 1.2s ease-in-out 0.4s infinite',
      },
    },
  },
  plugins: [],
};
