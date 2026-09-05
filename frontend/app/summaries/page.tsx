import Dashboard from "@/components/dashboard";

export const dynamic = "force-dynamic";

export default function SummariesPage() {
  return <Dashboard view="summaries" timeZone={process.env.TIMEZONE} />;
}
