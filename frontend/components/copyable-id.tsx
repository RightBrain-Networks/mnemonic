"use client";

export default function CopyableId({ value, label, copied, onCopy }: {
  value: string;
  label: string;
  copied: boolean;
  onCopy: () => void;
}) {
  return <span className="detail-id">
    <code>{value}</code>
    <button type="button" className={`icon-button detail-copy-id ${copied ? "is-copied" : ""}`}
      aria-label={label} title={copied ? "Copied" : label} onClick={onCopy}>
      <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d={copied ? "m5 12 4 4L19 6" : "M9 5V3h12v14h-3M3 7h12v14H3V7Z"} />
      </svg>
    </button>
  </span>;
}
