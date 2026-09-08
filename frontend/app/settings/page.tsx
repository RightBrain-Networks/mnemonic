import Dashboard from "@/components/dashboard";
import { backupMaximumBytes } from "@/lib/backups";

export const dynamic = "force-dynamic";

export default function SettingsPage() {
  return <Dashboard view="settings" timeZone={process.env.TIMEZONE} backupMaxBytes={backupMaximumBytes(process.env.MNEMONIC_BACKUP_MAX_BYTES)} />;
}
