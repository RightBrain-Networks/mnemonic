import Dashboard from "@/components/dashboard";
import { artifactMaximumBytes } from "@/lib/artifact-proxy";
import { backupMaximumBytes } from "@/lib/backups";

export const dynamic = "force-dynamic";

export default function DashboardLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <>
    <Dashboard
      timeZone={process.env.TIMEZONE}
      artifactMaxBytes={artifactMaximumBytes(process.env.MNEMONIC_ARTIFACT_MAX_BYTES)}
      backupMaxBytes={backupMaximumBytes(process.env.MNEMONIC_BACKUP_MAX_BYTES)}
    />
    {children}
  </>;
}
