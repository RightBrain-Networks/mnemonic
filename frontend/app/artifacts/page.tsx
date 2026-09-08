import Dashboard from "@/components/dashboard";
import { artifactMaximumBytes } from "@/lib/artifact-proxy";

export const dynamic = "force-dynamic";

export default function ArtifactsPage() {
  return <Dashboard view="artifacts" timeZone={process.env.TIMEZONE} artifactMaxBytes={artifactMaximumBytes(process.env.MNEMONIC_ARTIFACT_MAX_BYTES)} />;
}
