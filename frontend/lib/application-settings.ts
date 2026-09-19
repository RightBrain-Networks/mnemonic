export const HIDE_NEMO_STORAGE_KEY = "mnemonic.hide-nemo-logo";

export function hideNemoPreference(value: string | null): boolean {
  return value !== "false";
}

export const applicationSettingsInitializationScript = `(() => {
  let hidden = true;
  try { hidden = localStorage.getItem("${HIDE_NEMO_STORAGE_KEY}") !== "false"; } catch {}
  document.documentElement.dataset.hideNemo = String(hidden);
})();`;
