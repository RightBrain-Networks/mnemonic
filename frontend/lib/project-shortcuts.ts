// The digits select a project from the workspace picker: 1 through 9 for the first
// nine, then 0 for the tenth, which is the number row's own order. A workspace with
// more projects leaves the rest unbound rather than reaching for a modifier, because
// every modified function or digit combination is spoken for by some browser, window
// manager, or operating system.
export const PROJECT_SHORTCUT_LIMIT = 10;

export function projectShortcutKey(index: number): string | null {
  if (!Number.isInteger(index) || index < 0 || index >= PROJECT_SHORTCUT_LIMIT) return null;
  return String((index + 1) % 10);
}

// Only a bare digit names a project. The caller still has to refuse a press that means
// text, since unlike a function key a digit is something a person types.
export function projectShortcutIndex(key: string): number | null {
  if (!/^[0-9]$/.test(key)) return null;
  return (Number(key) + PROJECT_SHORTCUT_LIMIT - 1) % PROJECT_SHORTCUT_LIMIT;
}
