import type { Config } from "tailwindcss";

// Paleta inspirada em casas de apostas modernas (sem copiar assets).
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#F3F4F6",
        card: "#FFFFFF",
        primary: { DEFAULT: "#FF2638", 600: "#E01E2F", 50: "#FFF1F2" },
        ink: { DEFAULT: "#15171A", 2: "#667085", 3: "#98A2B3" },
        line: "#E5E7EB",
        success: { DEFAULT: "#12B76A", 50: "#ECFDF3" },
        warning: { DEFAULT: "#F79009", 50: "#FFFAEB" },
        danger: { DEFAULT: "#F04438", 50: "#FEF3F2" },
        info: { DEFAULT: "#2E90FA", 50: "#EFF8FF" },
      },
      borderRadius: { card: "14px" },
      boxShadow: {
        card: "0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.06)",
        hover: "0 6px 16px rgba(16,24,40,0.08)",
      },
      fontFamily: { sans: ["Inter", "Segoe UI", "system-ui", "sans-serif"] },
      transitionDuration: { DEFAULT: "180ms" },
    },
  },
  plugins: [],
} satisfies Config;
