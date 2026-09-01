import Dashboard from "@/components/dashboard";

export default function SettingsPage() {
  return <Dashboard view="settings" timeZone={process.env.TIMEZONE} />;
}
