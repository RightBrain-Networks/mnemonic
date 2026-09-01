import Dashboard from "@/components/dashboard";

export default function Page() {
  return <Dashboard timeZone={process.env.TIMEZONE} />;
}
