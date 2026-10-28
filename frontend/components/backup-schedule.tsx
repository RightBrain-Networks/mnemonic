"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { errorMessage } from "@/lib/api";
import { backupJson, backupPath, decodeBackupSchedule, type BackupSchedule } from "@/lib/backups";
import { objectValue } from "@/lib/wire-guards";

export default function BackupScheduleForm({ projectId, schedule, disabled, onSaved }: {
  projectId: string; schedule: BackupSchedule; disabled: boolean; onSaved: (schedule: BackupSchedule) => void;
}) {
  const [draft, setDraft] = useState(schedule);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const inFlight = useRef(false);
  useEffect(() => { if (!dirty) setDraft(schedule); }, [schedule, dirty]);
  const zones = [...new Set([Intl.DateTimeFormat().resolvedOptions().timeZone, "UTC", schedule.timezone])];
  function update(value: Partial<BackupSchedule>) { setDraft({ ...draft, ...value }); setDirty(true); setNotice(""); }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current) return;
    inFlight.current = true; setSaving(true); setError(""); setNotice("");
    try {
      const result = objectValue(await backupJson(`${backupPath(projectId)}/backup-settings`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(draft)
      }));
      if (result?.project_id !== projectId) throw new Error("The saved schedule could not be verified. Refresh to check it.");
      const saved = decodeBackupSchedule(result.schedule);
      setDraft(saved); setNotice("Nightly backup settings saved."); onSaved(saved); setDirty(false);
    } catch (error) { setError(`${errorMessage(error)} Reload this page to check the saved settings before trying again.`); }
    finally { inFlight.current = false; setSaving(false); }
  }
  return <form className="backup-schedule form-stack" onSubmit={(event) => void save(event)} aria-label="Nightly backup schedule">
    <h3>Nightly backups</h3>
    <label className="backup-checkbox"><input type="checkbox" checked={draft.enabled} disabled={disabled || saving} onChange={(event) => update({ enabled: event.target.checked })} />Enable nightly backups</label>
    <div className="backup-schedule-fields">
      <label className="field">Backup time<input type="time" required value={draft.time} disabled={disabled || saving} onChange={(event) => update({ time: event.target.value })} /></label>
      <label className="field">Time zone<select value={draft.timezone} disabled={disabled || saving} onChange={(event) => update({ timezone: event.target.value })}>{zones.map((zone) => <option key={zone}>{zone}</option>)}</select></label>
      <label className="field">Retention days<input type="number" min={0} max={36500} step={1} required value={Number.isNaN(draft.retention_days) ? "" : draft.retention_days} disabled={disabled || saving} onChange={(event) => update({ retention_days: event.target.valueAsNumber })} /></label>
    </div>
    <p className="field-hint">Keep backups for this many days. Set 0 to keep them indefinitely. Older backups are pruned only after a successful backup; the newest backup is always kept.</p>
    <div className="settings-actions"><button className="button button-secondary" disabled={disabled || saving || !dirty}>{saving ? "Saving schedule…" : "Save schedule"}</button></div>
    {notice && <p className="backup-notice" role="status">{notice}</p>}
    {error && <p className="error-notice" role="alert">{error}</p>}
  </form>;
}
