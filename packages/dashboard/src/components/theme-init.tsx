"use client";

import { useEffect } from "react";

export default function ThemeInit() {
  useEffect(() => {
    try {
      const saved = localStorage.getItem("nxt-theme");
      if (saved === "light") {
        document.documentElement.setAttribute("data-theme", "light");
      }
    } catch {
      // localStorage unavailable
    }
  }, []);

  return null;
}
