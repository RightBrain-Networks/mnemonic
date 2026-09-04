// F1 through F12 select a project from the workspace picker. Twelve is the whole
// function row, so a workspace with more projects simply leaves the rest unbound
// rather than reaching for a second modifier.
export const PROJECT_SHORTCUT_LIMIT = 12;

export function projectShortcutKey(index: number): string | null {
  if (!Number.isInteger(index) || index < 0 || index >= PROJECT_SHORTCUT_LIMIT) return null;
  return `F${index + 1}`;
}

// The picker is a native <select>, whose options carry text and nothing else, so the
// shortcut has to be named inside the option's own label.
export function projectShortcutOptionLabel(name: string, index: number): string {
  const key = projectShortcutKey(index);
  return key === null ? name : `${key} · ${name}`;
}

// Only a bare function key names a project; anything else, including F13 and up on
// keyboards that report it, leaves the event alone.
export function projectShortcutIndex(key: string): number | null {
  const match = /^F([1-9]|1[0-2])$/.exec(key);
  return match === null ? null : Number(match[1]) - 1;
}
