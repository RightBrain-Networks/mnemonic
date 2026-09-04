// The dashboard's shortcuts are bare keys, so they all stand aside for the same two
// things: a press that means text where the caret is, and a modal dialog that owns the
// keyboard outright while it is open.

export function typingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable;
}

export function dialogOpen(): boolean {
  return document.querySelector("dialog[open]") !== null;
}
