import { proxyBackup } from "@/lib/backup-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(request: Request, context: { params: Promise<{ path: string[] }> }): Promise<Response> {
  return proxyBackup(request, (await context.params).path, {
    MNEMONIC_DASHBOARD_ORIGINS: process.env.MNEMONIC_DASHBOARD_ORIGINS,
    MNEMONIC_BACKUP_URL: process.env.MNEMONIC_BACKUP_URL,
    MNEMONIC_BACKUP_TOKEN: process.env.MNEMONIC_BACKUP_TOKEN,
    MNEMONIC_BACKUP_MAX_BYTES: process.env.MNEMONIC_BACKUP_MAX_BYTES
  });
}

export { proxy as GET, proxy as POST };
