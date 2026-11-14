# Optional status reporting preference

For recurring status summaries, read `help(topic="query_activity usage")` and use
a bounded window or retained polling checkpoint. Select the facets relevant to
the requested report. Finish pagination and report coverage before advancing a
polling checkpoint. Summarize progress, blockers, and decisions with record links;
open individual records only when the bounded updates leave a material question.

Use `session_init()` when orientation is missing and read the selected project's
current settings. The owner chooses the reporting cadence and where a polling
checkpoint is retained; this module schedules or publishes nothing.
