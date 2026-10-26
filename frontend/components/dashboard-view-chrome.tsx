import InfoContainer from "@/components/info-container";

export default function DashboardViewChrome({
  eyebrow,
  title,
  subject,
  subjectDescription,
  description,
  info
}: {
  eyebrow?: string;
  title: string;
  subject?: string;
  subjectDescription?: string;
  description?: string;
  info?: { storageKey: string; label: string; content: string };
}) {
  return <section className="page-heading">
    <div>
      {eyebrow && <div className="eyebrow">{eyebrow}</div>}
      <h1>
        {title}<span className="heading-mark">{subject ? ":" : "."}</span>
        {subject && <>{" "}<span className="heading-subject">
          <span className="heading-subject-name">{subject}</span>
          {subjectDescription && <>
            <span className="heading-subject-separator">—</span>
            <span className="heading-subject-description">{subjectDescription}</span>
          </>}
        </span></>}
      </h1>
      {description && (info
        ? <InfoContainer storageKey={info.storageKey} label={info.label} description={description}>{info.content}</InfoContainer>
        : <p>{description}</p>)}
    </div>
  </section>;
}
