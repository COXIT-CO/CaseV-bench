/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    // Dense-analytics container: tight, wide, not the airy SaaS default (ADR 0012).
    container: {
      center: true,
      padding: "1.5rem",
      screens: { "2xl": "1600px" },
    },
    extend: {
      colors: {
        // All colours are driven by the OKLCH `L C H` channels defined per theme in
        // index.css. The `/ <alpha-value>` placeholder keeps Tailwind opacity modifiers
        // (e.g. `bg-primary/90`, `bg-destructive/10`) working.
        border: "oklch(var(--border) / <alpha-value>)",
        input: "oklch(var(--input) / <alpha-value>)",
        ring: "oklch(var(--ring) / <alpha-value>)",
        background: "oklch(var(--background) / <alpha-value>)",
        foreground: "oklch(var(--foreground) / <alpha-value>)",
        primary: {
          DEFAULT: "oklch(var(--primary) / <alpha-value>)",
          foreground: "oklch(var(--primary-foreground) / <alpha-value>)",
        },
        secondary: {
          DEFAULT: "oklch(var(--secondary) / <alpha-value>)",
          foreground: "oklch(var(--secondary-foreground) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "oklch(var(--muted) / <alpha-value>)",
          foreground: "oklch(var(--muted-foreground) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "oklch(var(--accent) / <alpha-value>)",
          foreground: "oklch(var(--accent-foreground) / <alpha-value>)",
        },
        destructive: {
          DEFAULT: "oklch(var(--destructive) / <alpha-value>)",
          foreground: "oklch(var(--destructive-foreground) / <alpha-value>)",
        },
        popover: {
          DEFAULT: "oklch(var(--popover) / <alpha-value>)",
          foreground: "oklch(var(--popover-foreground) / <alpha-value>)",
        },
        card: {
          DEFAULT: "oklch(var(--card) / <alpha-value>)",
          foreground: "oklch(var(--card-foreground) / <alpha-value>)",
        },
        // Semantic / status palette — color encodes meaning, not decoration (ADR 0012,
        // brief §2). Each carries a solid tone + a low-chroma `-subtle` badge fill.
        // Status mapping: queued → neutral, running → warning, done → success,
        // failed → danger; scored → success, unscored → neutral; prediction ok/error →
        // success/danger.
        success: {
          DEFAULT: "oklch(var(--success) / <alpha-value>)",
          subtle: "oklch(var(--success-subtle) / <alpha-value>)",
        },
        danger: {
          DEFAULT: "oklch(var(--danger) / <alpha-value>)",
          subtle: "oklch(var(--danger-subtle) / <alpha-value>)",
        },
        warning: {
          DEFAULT: "oklch(var(--warning) / <alpha-value>)",
          subtle: "oklch(var(--warning-subtle) / <alpha-value>)",
        },
        neutral: {
          subtle: "oklch(var(--neutral-subtle) / <alpha-value>)",
        },
        // Fixed overlay legend (red = prediction, green = ground truth).
        overlay: {
          prediction: "oklch(var(--overlay-prediction) / <alpha-value>)",
          gt: "oklch(var(--overlay-gt) / <alpha-value>)",
        },
      },
      borderRadius: {
        // ~10px cards, tighter controls (mockup).
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        // Inter for prose/labels; JetBrains Mono for model slugs, JSON, metric cells.
        sans: ['"Inter Variable"', "Inter", "system-ui", "sans-serif"],
        mono: [
          '"JetBrains Mono Variable"',
          '"JetBrains Mono"',
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
