"use client";

import { useEffect, useRef, useState } from "react";
import {
  resolvedTheme,
  themePreference,
  themePreferences,
  themeStorageKey,
  type ThemePreference
} from "@/lib/theme-preference";

const themeLabels: Record<ThemePreference, string> = {
  auto: "Auto",
  dark: "Dark",
  light: "Light"
};

function ThemeIcon({ preference }: { preference: ThemePreference }) {
  if (preference === "auto") {
    return <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 3.5a8.5 8.5 0 0 1 0 17Z" fill="currentColor" stroke="none" />
    </svg>;
  }
  if (preference === "dark") {
    return <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M19.4 15.2A8.5 8.5 0 0 1 8.8 4.6a8.5 8.5 0 1 0 10.6 10.6Z" />
    </svg>;
  }
  return <svg viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="12" cy="12" r="3.5" />
    <path d="M12 2v2.2M12 19.8V22M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M2 12h2.2M19.8 12H22M4.9 19.1l1.6-1.6M17.5 6.5l1.6-1.6" />
  </svg>;
}

type ThemeTransitionDocument = Document & {
  startViewTransition?: (update: () => void) => unknown;
};

let fallbackTransitionTimeout: number | undefined;

function applyTheme(
  preference: ThemePreference,
  systemPrefersDark: boolean,
  animate = true
) {
  const root = document.documentElement;
  const theme = resolvedTheme(preference, systemPrefersDark);
  const updateTheme = () => {
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
  };

  if (root.dataset.theme === theme) {
    root.style.colorScheme = theme;
    return;
  }
  if (!animate || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    updateTheme();
    return;
  }

  const transitionDocument = document as ThemeTransitionDocument;
  if (transitionDocument.startViewTransition) {
    transitionDocument.startViewTransition(updateTheme);
    return;
  }

  root.classList.add("theme-crossfade");
  void root.offsetWidth;
  updateTheme();
  window.clearTimeout(fallbackTransitionTimeout);
  fallbackTransitionTimeout = window.setTimeout(() => {
    root.classList.remove("theme-crossfade");
  }, 320);
}

export default function ThemeSelector() {
  const [preference, setPreference] = useState<ThemePreference>("auto");
  const preferenceRef = useRef<ThemePreference>("auto");

  useEffect(() => {
    const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
    let savedPreference: ThemePreference = "auto";
    try {
      savedPreference = themePreference(localStorage.getItem(themeStorageKey));
    } catch {
      // localStorage can be unavailable in privacy-restricted browsing contexts.
    }
    preferenceRef.current = savedPreference;
    setPreference(savedPreference);
    applyTheme(savedPreference, systemTheme.matches, false);

    const followSystemTheme = (event: MediaQueryListEvent) => {
      if (preferenceRef.current === "auto") applyTheme("auto", event.matches);
    };
    systemTheme.addEventListener("change", followSystemTheme);
    return () => systemTheme.removeEventListener("change", followSystemTheme);
  }, []);

  function chooseTheme(nextPreference: ThemePreference) {
    preferenceRef.current = nextPreference;
    setPreference(nextPreference);
    try {
      localStorage.setItem(themeStorageKey, nextPreference);
    } catch {
      // Applying the choice still works for the current page when storage is unavailable.
    }
    applyTheme(
      nextPreference,
      window.matchMedia("(prefers-color-scheme: dark)").matches
    );
  }

  return <fieldset className="theme-selector">
    <legend className="sr-only">Color theme</legend>
    {themePreferences.map((option) => <label key={option}>
      <input
        type="radio"
        name="theme"
        value={option}
        aria-label={themeLabels[option]}
        checked={preference === option}
        onChange={() => chooseTheme(option)}
      />
      <span><ThemeIcon preference={option} /></span>
    </label>)}
  </fieldset>;
}
