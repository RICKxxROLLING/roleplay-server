/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0b0a10",
          900: "#12111a",
          850: "#181724",
          800: "#1f1d2e",
          700: "#2b2840",
        },
        accent: "#a78bfa",
      },
    },
  },
  plugins: [],
};
