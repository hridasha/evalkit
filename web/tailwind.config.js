/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#090b11",
        panel: "#12161f",
        line: "#232b3c",
        muted: "#8791a7",
        accent: "#7c8cff",
        ok: "#3ecf8e",
        bad: "#ff6b62",
        warn: "#f5a524",
      },
      fontFamily: { mono: ["Consolas", "ui-monospace", "monospace"] },
      keyframes: {
        "slide-up": { from: { opacity: 0, transform: "translateY(8px)" }, to: { opacity: 1, transform: "none" } },
      },
      animation: { "slide-up": "slide-up .25s ease-out" },
    },
  },
  plugins: [],
};
