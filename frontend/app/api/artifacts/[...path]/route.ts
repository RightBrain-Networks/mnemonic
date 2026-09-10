import { proxyArtifact } from "@/lib/artifact-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(request: Request, context: { params: Promise<{ path: string[] }> }): Promise<Response> {
  return proxyArtifact(request, (await context.params).path, {
    MNEMONIC_DASHBOARD_ORIGINS: process.env.MNEMONIC_DASHBOARD_ORIGINS,
    MNEMONIC_API_URL: process.env.MNEMONIC_API_URL,
    MNEMONIC_API_KEY: process.env.MNEMONIC_API_KEY,
    MNEMONIC_ARTIFACT_MAX_BYTES: process.env.MNEMONIC_ARTIFACT_MAX_BYTES
  });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
