export const themeStorageKey = "mnemonic.theme";

export const themePreferences = ["auto", "dark", "light"] as const;

export type ThemePreference = (typeof themePreferences)[number];
export type ResolvedTheme = Exclude<ThemePreference, "auto">;

export function themePreference(value: string | null | undefined): ThemePreference {
  return themePreferences.includes(value as ThemePreference)
    ? value as ThemePreference
    : "auto";
}

export function resolvedTheme(
  preference: ThemePreference,
  systemPrefersDark: boolean
): ResolvedTheme {
  return preference === "auto"
    ? systemPrefersDark ? "dark" : "light"
    : preference;
}

export const themeInitializationScript = `(() => {
  const root = document.documentElement;
  let preference = "auto";
  try {
    const saved = localStorage.getItem("${themeStorageKey}");
    if (saved === "auto" || saved === "dark" || saved === "light") preference = saved;
  } catch {}
  const systemPrefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const theme = preference === "auto" ? (systemPrefersDark ? "dark" : "light") : preference;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
})();`;
