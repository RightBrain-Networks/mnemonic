import { redirect } from "next/navigation";

// Keep saved work links usable after moving the library off the home route.
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const query = await searchParams;
  if (typeof query.work === "string") {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) if (typeof value === "string") params.set(key, value);
    redirect(`/work-items?${params}`);
  }
  return null;
}
