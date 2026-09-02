import Dashboard from "@/components/dashboard";

export const dynamic = "force-dynamic";

export default function AttentionPage() {
  return <Dashboard view="attention" timeZone={process.env.TIMEZONE} />;
}
