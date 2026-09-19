"use client";

import { useEffect, useId, useRef, useState } from "react";
import { HIDE_NEMO_STORAGE_KEY, hideNemoPreference } from "@/lib/application-settings";

export default function ApplicationSettings({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [hideNemo, setHideNemo] = useState(true);

  useEffect(() => {
    const dialog = ref.current!;
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const sync = () => {
      let hidden = true;
      try { hidden = hideNemoPreference(localStorage.getItem(HIDE_NEMO_STORAGE_KEY)); } catch { /* optional */ }
      setHideNemo(hidden);
      document.documentElement.dataset.hideNemo = String(hidden);
    };
    sync();
    window.addEventListener("storage", sync);
    dialog.showModal();
    return () => {
      window.removeEventListener("storage", sync);
      dialog.close();
      if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    };
  }, []);

  function toggle(hidden: boolean) {
    setHideNemo(hidden);
    document.documentElement.dataset.hideNemo = String(hidden);
    try { localStorage.setItem(HIDE_NEMO_STORAGE_KEY, String(hidden)); } catch { /* optional */ }
  }

  return <dialog ref={ref} className="application-settings-drawer" aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => {
      if (event.target !== event.currentTarget) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose();
    }}>
    <header className="application-settings-header">
      <div><span className="section-label">PREFERENCES</span><h2 id={titleId}>Application settings</h2></div>
      <button type="button" className="icon-button" aria-label="Close application settings" onClick={onClose} autoFocus>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
      </button>
    </header>
    <div className="application-settings-body">
      <p>These preferences apply to all projects in this browser.</p>
      <label className="application-setting">
        <span><strong>Hide Nemo logo</strong><span className="field-hint">Hide the sidebar robot and “Keeping your agents on the same page.”</span></span>
        <input className="settings-switch" type="checkbox" role="switch" aria-label="Hide Nemo logo" checked={hideNemo} onChange={(event) => toggle(event.target.checked)} />
      </label>
    </div>
  </dialog>;
}
