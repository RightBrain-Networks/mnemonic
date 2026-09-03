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
        checked={preference === option}
        onChange={() => chooseTheme(option)}
      />
      <span>{themeLabels[option]}</span>
    </label>)}
  </fieldset>;
}
