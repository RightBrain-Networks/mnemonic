# Assign work priority deliberately

Use this rubric before choosing `priority` for `create_work` or an authorized
priority change through `update_work`. Priority is an integer from 0 to 100;
higher values sort first in ready discovery. It expresses the consequence of
delaying this objective in this project. It does not grant execution authority
or replace lifecycle, blockers, human gates, or leases.

## Choose the consequence band first

Honor a priority explicitly supplied by the current user. Otherwise use the
bands below, adapting the examples to the project's users, critical workflows,
and operating commitments. Start at the anchor, not an arbitrary number or the
API default. These are consequence anchors, not an exhaustive category list.

| Range | Anchor | Consequence of delay and typical examples |
| --- | --- | --- |
| 0–9 | 5 | Optional polish with no meaningful effect on task completion, correctness, or risk: gold plating, stylistic preferences, cosmetic nits, speculative abstractions. |
| 10–29 | 20 | Small, demonstrated benefit: minor usability friction, documentation gaps with an obvious workaround, local developer convenience, modest cleanup with a concrete maintenance benefit. |
| 30–49 | 40 | Material improvement to an otherwise working system: a useful enhancement tied to a project goal, recurring manual toil, measurable nonblocking performance degradation, reliability or maintenance work addressing evidenced future risk. |
| 50–69 | 60 | Existing functionality is degraded or partly broken, but users can still achieve the outcome with a practical workaround: intermittent user-facing technical errors, a broken secondary workflow, significant accessibility or rendering friction. |
| 70–90 | 80 | Essential functionality is unavailable or seriously incorrect with no practical workaround: failed login, checkout, or saving; a release blocked by a demonstrated defect; bounded, recoverable data integrity errors. |
| 91–99 | 95 | Confirmed applicable security vulnerabilities; ongoing or imminent data loss, silent corruption, or exposure; severe widespread outage. These displace ordinary backlog work. Use 91 for a contained vulnerability with limited impact, and move toward 99 as exposure, reach, irreversibility, or urgency increases. |
| 100 | 100 | Immediate incident response: active exploitation, destructive corruption in progress, or a critical outage requiring interruption of current work. Reserve 100 for evidenced emergencies. |

A security label alone is insufficient: distinguish a confirmed vulnerability
in the project's actual code or deployed configuration from an unverified
scanner finding or general hardening suggestion. Confirmed applicable
vulnerabilities belong above 90; preventive hardening follows its demonstrated
risk. A cosmetic nit stays below 10 only while it has no functional impact.

## Refine using evidence, not task labels

Before settling on a score, identify the affected outcome, who is affected and
how often, whether a practical workaround exists, whether damage is reversible,
and what gets worse if the work waits. Use facts already available; capture
unknowns rather than turning a save request into an open-ended investigation.

- **Impact and recovery set the band.** Incorrect stored data is different from
  an incorrect display. A retry is a workaround only if it reliably recovers the
  user's outcome without unacceptable effort, risk, or lost work. An essential
  workflow broken for a small cohort still belongs in 70–90.
- **Reach, frequency, and time pressure refine it.** Move above the anchor for
  broad or frequent impact, accumulating damage, or a real approaching deadline;
  below it for rare, contained impact or effective mitigation. Prefer five-point
  steps within the band; use its boundary when appropriate (for example, 91 or
  99). Cross a band only when the consequence fits the new band. Do not add up
  overlapping symptoms or invent a deadline.
- **Unknown is not low priority.** Separate observed facts from suspected harm.
  A credible risk of severe harm warrants a provisional high priority for
  verification or containment, with the missing evidence and reassessment
  trigger stated. Speculation alone does not establish a vulnerability or
  emergency. Do not multiply severity by an arbitrary confidence percentage.
- **Project context supplies the meaning.** Judge accessibility, performance,
  compliance, infrastructure, and project-specific concerns by the outcome
  they threaten. A demonstrated commitment or dependency on urgent work can
  justify a higher band; being an enhancement or internal task does not impose
  a ceiling. Record an established dependency as a `blocks` edge separately.
- **Effort is separate.** Neither an easy fix nor an interesting or difficult
  implementation makes an item urgent. Do not inherit a parent's priority or
  copy nearby legacy scores without assessing this objective's consequences.

For example, a brief flash of unstyled content (FOUC) with no usability impact
is 5; repeated flashing that materially disrupts reading may be 60; rendering
that prevents an essential task with no workaround is 80. A technical error
users can reliably retry may be 60; one that prevents all saves is 80; a save
that silently destroys records is 95. A useful optional feature may be 40,
while a feature needed to meet a concrete imminent operational commitment may
be 80. These examples assume the stated impact, not a universal score for the
label.

## Persist a brief rationale and keep comparisons meaningful

Include the selected `priority` explicitly in the frozen `create_work` intent.
In the initial checkpoint's context, record one or two concise sentences with
the score, concrete impact, decisive workaround or urgency fact, and material
uncertainty. For example: "Priority 80: invoice submission fails for all users
and there is no workaround; drafts remain intact." This is a decision summary,
not private chain-of-thought or a new metadata field. When preserving a user's
explicit score, attribute it instead of inventing supporting evidence.

Compare with a few relevant items already recalled when available: would it be
reasonable to do this before them, and why? Prefer ties for comparable impact.
Do not normalize to the existing 10–35 cluster, force a uniform distribution,
or raise scores merely to jump the queue. The bands govern even if historical
items were scored inconsistently.

Reassess when evidence changes impact, exposure, mitigation, or a real deadline,
within the scope of authorized work. Explain a changed score in a context
checkpoint and use the existing versioned `update_work` workflow. Reading or
resuming work does not itself authorize reprioritization or a backlog rewrite.
Freeze the score and rationale before sending a protected intent; unknown-outcome
retries must retain the original arguments and operation UUID.
