// The dashboard presents binary megabytes; REST retains exact byte counts.
export const TRANSCRIPT_MEGABYTE = 1024 * 1024;
export function transcriptMegabytes(bytes: number): string {
  return (bytes / TRANSCRIPT_MEGABYTE).toFixed(20).replace(/\.?0+$/, "");
}
export function transcriptBytes(megabytes: string): number | null {
  if (!/^\d+(?:\.\d{1,20})?$/.test(megabytes)) return null;
  const bytes = Math.round(Number(megabytes) * TRANSCRIPT_MEGABYTE);
  return Number.isSafeInteger(bytes) && bytes > 0 ? bytes : null;
}
