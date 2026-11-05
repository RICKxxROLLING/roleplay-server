/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        serif: ["Literata", "Georgia", "serif"],
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
      },
      // Warm throughout. "slate" keeps its name so every panel takes the new look, with
      // every step used for text at 4.5:1 or better on ink-950.
      colors: {
        ink: {
          950: "#16130F",
          900: "#1C1813",
          850: "#211C16",
          800: "#2E2820",
          700: "#3A3227",
        },
        slate: {
          100: "#F4EEE3",
          200: "#EDE6DA",
          300: "#D9CFBE",
          400: "#C2B6A3",
          500: "#ADA190",
          600: "#A0947F",
          700: "#6E6455",
        },
        accent: "#D9A766",
      },
    },
  },
  plugins: [],
};
