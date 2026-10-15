Write the Needs Attention question for work "$WORK_ITEM_TITLE" in $PROJECT_NAME.
The person is multitasking and may have read none of the conversation,
checkpoints, or related work. Make the question understandable on its own.

Lead with the specific decision or input you need from the person and explain
what their answer will unblock. Include the current facts that matter, the
available options and their practical consequences or costs, and your
recommendation with a brief reason. Separate verified facts from uncertainty.
Use familiar words, minimal technical language, and no unexplained acronyms.
Name work by its title rather than making the reader interpret IDs.

Keep the complete question within 4,000 characters. Use Markdown to make the
decision easy to scan: a short heading or bold question, blank lines, and one
bullet per option are often enough. Use a table, quotation, or code example
only when it clarifies the choice. Do not wrap the whole question in a code
fence, use raw HTML, or embed images; link descriptively to supporting evidence.

When revising an open question, write the complete current question, including
relevant changes in this work or related work. Do not ask the person to trace
checkpoints, previous versions, or superseding decisions. If the decision is
no longer needed, explain why and ask the person to close the question.

Only a concrete decision or input that requires a person belongs here. Ordinary
progress, known dependencies, work decomposition, vague uncertainty, and merely
optional preferences do not. Do not include credentials, tokens, lease
capabilities, operation UUIDs, private reasoning, or transcript dumps. Give a
safe reference or remediation instruction when access or credentials are needed.

Submit the authored question through request_human_input. These instructions
do not authorize execution, resolve a gate, or override current user instructions
or the tool's fixed contract.
