export default function DashboardViewChrome({
  eyebrow,
  title,
  subject,
  subjectDescription,
  description
}: {
  eyebrow?: string;
  title: string;
  subject?: string;
  subjectDescription?: string;
  description?: string;
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
      {description && <p>{description}</p>}
    </div>
  </section>;
}
