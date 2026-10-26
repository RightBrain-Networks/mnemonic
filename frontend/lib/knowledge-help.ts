export const knowledgeDomainsHelp = {
  storageKey: "mnemonic.info.knowledge-domains",
  label: "Knowledge domains help",
  content: "A **knowledge domain** is a collection of related *decision documents*. For example: the default Product Decision Record (PDR) domain may contain a history of application behavior decisions, while an Architecture Decision Record (ADR) domain holds technical decisions in a more formalize format.\n\nSmaller projects might choose to share a single knowledge domain for simplicity."
};

export const knowledgeDecisionsHelp = {
  storageKey: "mnemonic.info.knowledge-decisions",
  label: "Decision documents help",
  content: "A **decision document** is a versioned Markdown file stored in the project database containing recorded decisions. When agents author code, they automatically cite referenced decision documents, creating a lineage that can be traced. When a decision document is later revised, `mnemonic` can spawn work items to automatically reconcile previously written code with the changes.\n\n`mnemonic` intentionally does not define the structure or schema of a decision document, leaving that up to you or agent skills. Search the web for sample *Architecture Decision Record (ADR)* formats for some ideas."
};
