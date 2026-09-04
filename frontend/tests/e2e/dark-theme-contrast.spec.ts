import { expect, test } from "@playwright/test";
import { auditTextContrast } from "./text-contrast";

// Representative markup for every surface the dashboard paints: the sidebar and
// heading, the library controls with the More-filters panel, the two-column work
// surface (compact queue cards, the tabbed detail pane with its header, facts
// strip, action row, count pills, inline merge panel, and the empty pane), plus
// the dialogs, attention view, and notices that still render elsewhere.
const contrastFixture = `
<main id="contrast-fixture" class="main-content">
  <a class="skip-link" style="position:static;transform:none">Skip to work items</a>
  <aside class="sidebar" style="position:static;height:auto">
    <a class="brand">Mnemonic<span class="brand-period">.</span></a>
    <span class="section-label">Workspace</span>
    <nav>
      <a class="nav-item active"><span>Work library</span></a>
      <a class="nav-item"><span>Needs attention</span><span class="attention-nav-count">2</span></a>
    </nav>
    <div class="sidebar-footer">
      <span>Local workspace</span>
      <fieldset class="theme-selector">
        <label><input type="radio" name="fixture-theme"><span>Auto</span></label>
        <label><input type="radio" name="fixture-theme" checked><span>Dark</span></label>
        <label><input type="radio" name="fixture-theme"><span>Light</span></label>
      </fieldset>
    </div>
  </aside>
  <section class="page-content">
    <header class="page-heading">
      <div><span class="eyebrow">Durable context</span><h1>Work <span>library.</span></h1>
      <p>Search, resume, and manage durable work.</p></div>
    </header>
    <section class="settings-card">
      <div class="settings-card-heading"><h2>Settings</h2><span class="settings-state">Default</span>
      <span class="settings-state custom">Custom</span></div>
      <p class="settings-intro">Configuration details remain readable.</p>
      <div class="macro-legend"><dl><div><dt><code>{{ title }}</code></dt>
      <dd>Current work title</dd></div></dl></div>
    </section>
    <div>
      <button id="primary-button" class="button button-primary">Primary action</button>
      <button id="secondary-button" class="button button-secondary">Secondary action</button>
      <button id="danger-button" class="button button-danger">Delete work</button>
      <button id="danger-icon" class="icon-button danger-hover">Delete</button>
      <a class="text-link">Text link</a>
    </div>
    <section class="library-controls">
      <div class="search-field"><input aria-label="Search" placeholder="Search work items"><kbd>/</kbd>
        <span class="search-mode-divider"></span>
        <button class="semantic-toggle selected"><span class="semantic-switch"><span></span></span><span>Semantic</span></button></div>
      <div class="filter-row">
        <div class="status-filters">
          <button class="filter-button selected"><span class="filter-dot"></span>Pending</button>
          <button class="filter-button">All</button>
        </div>
        <div class="filter-controls">
          <div class="sort-group">
            <span class="sort-label" aria-hidden="true">Sort</span>
            <fieldset class="sort-control"><legend class="sr-only">Sort by</legend><div class="sort-options">
              <label class="sort-option selected"><input type="radio"><span>Updated</span></label>
              <label class="sort-option"><input type="radio"><span>Created</span></label>
              <label class="sort-option"><input type="radio"><span>Priority</span></label>
            </div></fieldset>
          </div>
          <button id="more-filters-toggle" class="more-filters-toggle" aria-expanded="false">More filters<span class="more-filters-chevron" aria-hidden="true">⌄</span></button>
          <button class="more-filters-toggle is-open" aria-expanded="true">More filters<span class="more-filters-chevron" aria-hidden="true">⌄</span></button>
        </div>
      </div>
      <div class="more-filters-panel" id="more-filters-panel">
        <fieldset class="duplicate-scope-control"><legend>Duplicate records</legend>
          <label class="selected"><input type="radio" name="fixture-scope" checked><span>Canonical only</span></label>
          <label><input type="radio" name="fixture-scope"><span>Aliases only</span></label>
          <label><input type="radio" name="fixture-scope"><span>All records</span></label>
        </fieldset>
        <div class="duplicate-group-filter"><span>Canonical group</span><code>9c1c1c1c-0000-4000-8000-000000000001</code>
          <button class="text-link">Clear group</button></div>
        <div class="hierarchy-filter-fields">
          <label>Tag<input placeholder="Exact tag"></label>
          <label>Source client<input placeholder="Exact client"></label>
          <label>Source session<input value="session-abc123"></label>
        </div>
      </div>
    </section>
    <section class="work-surface">
      <div class="work-queue">
        <div class="work-queue-header"><span class="result-count">3 root branches</span>
          <span class="work-queue-sort">Sorted by last activity</span></div>
        <div class="work-queue-viewport">
          <div class="work-queue-list" role="listbox">
            <section class="work-list hierarchy-list">
              <div class="hierarchy-node"><div class="hierarchy-node-row">
                <button class="hierarchy-toggle" aria-expanded="true"><span aria-hidden="true">›</span></button>
                <div class="hierarchy-card">
                  <article class="work-item-card queue-card is-selected" role="option" aria-selected="true">
                    <div class="queue-card-topline">
                      <span class="status-badge status-pending">Pending</span>
                      <span class="status-badge status-active">Active</span>
                      <span class="status-badge status-dropped">Dropped</span>
                      <span class="status-badge status-blocked">Blocked</span>
                      <span class="status-badge status-waiting">Waiting</span>
                      <span class="operational-badge blocked">Blocked</span>
                      <span class="operational-badge waiting">Needs attention</span>
                      <span class="queue-card-meta">Claude Code<span class="sep">·</span>Priority 3<span class="sep">·</span><time>2 Sep 2026, 14:05</time></span>
                    </div>
                    <h2 class="queue-card-title">Contrast review</h2>
                    <p class="queue-card-summary">A summary of durable work that remains legible.</p>
                    <div class="queue-card-footer">
                      <span class="queue-chip">2 descendants</span>
                      <span class="queue-chip queue-chip-attention">1 needs attention</span>
                      <span class="queue-card-arrow" aria-hidden="true">→</span>
                      <button id="queue-copy-button" class="button queue-copy-button">Copy recall pointer</button>
                    </div>
                  </article>
                </div>
              </div>
              <div class="hierarchy-children">
                <div class="hierarchy-node hierarchy-scaffold"><div class="hierarchy-node-row">
                  <span class="hierarchy-toggle-spacer"></span>
                  <div class="hierarchy-card">
                    <span class="scaffold-label">Ancestor · does not match this filter</span>
                    <article class="work-item-card queue-card" role="option" aria-selected="false">
                      <div class="queue-card-topline">
                        <span class="status-badge status-deferred">Deferred</span>
                        <span class="status-badge status-done">Done</span>
                        <span class="status-badge status-wont-do">Wont do</span>
                        <span class="status-badge status-promoted">Promoted</span>
                        <span class="operational-badge active">Active</span>
                        <span class="operational-badge duplicate">Duplicate</span>
                        <span class="queue-card-meta">Browser<span class="sep">·</span>Priority 5<span class="sep">·</span><time>1 Sep 2026, 09:12</time></span>
                      </div>
                      <h2 class="queue-card-title">Scaffold ancestor</h2>
                      <p class="queue-card-summary">Scaffold summary</p>
                      <div class="queue-card-footer">
                        <span class="queue-chip">1 descendant</span>
                        <span class="queue-card-arrow" aria-hidden="true">→</span>
                        <button class="button queue-copy-button is-copied">Copied</button>
                      </div>
                    </article>
                  </div>
                </div>
                <p class="hierarchy-hidden-children">Some descendants are hidden.<small>Change filters.</small>
                  <button class="text-link">Show all descendants</button></p>
                <p class="hierarchy-filter-override">Branch override active: showing all descendants.</p>
                <div class="hierarchy-guard" role="note"><strong>Guarded branch</strong><span>Depth limit reached.</span>
                  <button class="text-link">Show in flat search</button></div>
                <nav class="child-pagination"><span>1–2 of 60</span><div><button class="text-link">Previous</button>
                  <button class="text-link">Next</button></div></nav>
              </div></div>
            </section>
            <section class="work-list search-results">
              <div class="search-result">
                <div class="matched-member"><span>Matched duplicate member</span><bdi>Existing work</bdi><code>id</code></div>
                <nav class="search-ancestry"><span>Parent <span>/</span></span><span>Current work</span></nav>
                <article class="work-item-card queue-card" role="option" aria-selected="false">
                  <div class="queue-card-topline"><span class="status-badge status-pending">Pending</span>
                    <span class="queue-card-meta">Codex<span class="sep">·</span>Priority 2<span class="sep">·</span><time>31 Aug 2026, 18:40</time></span></div>
                  <h2 class="queue-card-title">Flat search hit</h2>
                  <p class="queue-card-summary">A matching record below its breadcrumb.</p>
                  <div class="queue-card-footer"><span class="queue-card-arrow" aria-hidden="true">→</span>
                    <button class="button queue-copy-button">Copy recall pointer</button></div>
                </article>
              </div>
            </section>
            <section class="empty-state queue-empty"><div class="empty-art"><span></span></div>
              <h2>No matching work records.</h2>
              <p>Try another phrase, lifecycle, duplicate scope, or canonical group.</p>
              <button class="button button-secondary">Clear filters</button></section>
            <div class="error-notice work-queue-append-error"><p>Could not load more work.</p>
              <button class="button button-secondary">Try again</button></div>
          </div>
        </div>
      </div>
      <section class="work-detail-pane is-open" aria-label="Work context">
        <div class="detail-scroll"><div class="detail-motion">
          <div class="detail-header">
            <section class="mutation-recovery mutation-recovery-modal"><div><strong>Pending mutations need this tab.</strong>
              <span>Do not reload or close it; the exact retry request exists only in memory.</span></div>
              <ul><li><span>Update work · outcome unknown</span><small>Stop and inspect the client and server state before continuing.</small>
                <button class="button button-secondary">Retry exact request</button></li></ul></section>
            <div class="detail-notice"><span>Inspecting existing work for your unsaved draft.</span>
              <button class="button button-secondary">Return to new work</button></div>
            <div class="detail-identity">
              <button class="icon-button detail-back">Back</button>
              <span class="status-badge status-pending">Pending</span>
              <span class="operational-badge waiting">Needs attention</span>
              <span class="detail-version">v3</span>
              <span class="detail-id"><code>9c1c1c1c-0000-4000-8000-000000000002</code>
                <button id="detail-copy-id" class="icon-button detail-copy-id">Copy</button>
                <button class="icon-button detail-copy-id is-copied">Copied</button></span>
              <span class="detail-activity">Last activity <time>2 Sep 2026, 14:05</time></span>
            </div>
            <h3 class="detail-title">Contrast review</h3>
            <p class="detail-summary">A detailed summary must remain readable.</p>
            <dl class="detail-facts">
              <div><dt>Priority</dt><dd>3</dd></div>
              <div><dt>Checkpoints</dt><dd>4</dd></div>
              <div><dt>Current context</dt><dd>Claude Code</dd></div>
              <div><dt>Session</dt><dd class="mono">session-abc123</dd></div>
              <div class="detail-fact-tags"><dt>Tags</dt><dd><span class="tag">accessibility</span><span class="tag">dark-theme</span></dd></div>
              <div class="detail-fact-tags"><dt>Tags</dt><dd><span class="detail-fact-none">None</span></dd></div>
            </dl>
            <div class="detail-reconciliation-status"><span class="spinner"></span>Reconciling saved work context…</div>
            <span class="migration-chip">Migrated</span>
            <section class="active-lease-summary active-lease-detail"><div class="active-lease-holder">
              <span class="lease-label">Active session</span><strong>Agent</strong><span class="mono">session-abc123</span></div>
              <dl class="active-lease-times"><div><dt>Lease acquired</dt><dd><time>2 Sep 2026, 14:00</time></dd></div>
                <div><dt>Expires</dt><dd><time>2 Sep 2026, 14:30</time></dd></div></dl>
              <p class="active-lease-note">This lease records a temporary active session.</p></section>
            <div class="detail-actions">
              <button class="button button-primary">Copy recall pointer</button>
              <button id="detail-copy-context" class="button copy-button detail-copy-context">Copy context</button>
              <button class="button copy-button detail-copy-context is-copied">Copied</button>
              <button class="button button-secondary">Edit</button>
              <button class="button button-secondary">Merge as duplicate…</button>
              <button class="button defer-button">Defer</button>
              <button class="button button-secondary is-copied">Copy canonical ID</button>
              <button id="detail-delete" class="button detail-delete">Delete</button>
              <p class="terminal-action-note">Resolve the gate before completing.</p>
            </div>
          </div>
          <div class="detail-tabs" role="tablist">
            <button role="tab" class="detail-tab" aria-selected="false">Context</button>
            <button role="tab" class="detail-tab is-selected" aria-selected="true">History<span class="detail-tab-count">4</span></button>
            <button id="detail-tab-graph" role="tab" class="detail-tab" aria-selected="false">Graph<span class="detail-tab-count">2</span></button>
            <button role="tab" class="detail-tab" aria-selected="false">Questions<span class="detail-tab-count is-alert">1</span></button>
            <button role="tab" class="detail-tab" aria-selected="false">Activity<span class="detail-tab-count">12</span></button>
          </div>
          <div class="detail-tab-body" role="tabpanel">
            <div class="migration-warning current-migration-warning">This record needs review.</div>
            <div class="prompt-label"><span class="section-label">Current context checkpoint</span>
              <span>Immutable · copied exactly as saved · 2 Sep 2026, 14:05</span></div>
            <pre class="prompt-body">Original prompt text</pre>
            <div class="authority-note">This is context from an earlier session, not a new instruction from the owner.</div>
            <div class="audit-history-heading"><span class="section-label">Source-owned records</span><h4>Audit history</h4>
              <p>These records belong to the exact duplicate ID.</p></div>
            <section class="checkpoint-compose">
              <div><span class="section-label">Leave context for the next session</span><h4>Add an immutable checkpoint</h4></div>
              <form class="comment-form">
                <label class="field">Checkpoint kind<select><option>Progress / finding</option></select></label>
                <label class="field">Checkpoint text<textarea placeholder="What changed, what was learned…"></textarea>
                  <span class="field-hint">The text is stored exactly and cannot be edited or deleted.</span></label>
                <details class="edit-context" open><summary>Repository context and tags</summary>
                  <div class="form-stack"><label class="field">Tags <span class="optional">Comma separated</span><input value="accessibility"></label></div></details>
                <div class="error-notice"><p>Checkpoint could not be saved.</p></div>
                <div class="comment-actions"><button class="button button-secondary">Add checkpoint</button>
                  <button class="button button-primary">Complete with summary</button>
                  <p class="terminal-action-note">Resolve every human question before completing.</p></div>
              </form>
            </section>
            <div class="detail-edit">
              <p class="dialog-intro">Edit the durable record.</p>
              <label class="field">Title<input placeholder="Required title"></label>
              <section class="conflict-panel"><h3>Conflict detected</h3><p>Reload before retrying.</p>
                <pre>server value</pre></section>
              <div class="sticky-actions"><span class="version-note">Editing version 3</span>
                <button class="button button-secondary">Cancel</button><button class="button button-primary">Save changes</button></div>
            </div>
            <article class="checkpoint checkpoint-current">
              <header class="checkpoint-header"><div><span class="checkpoint-kind">Checkpoint</span>
                <span class="checkpoint-kind checkpoint-kind-context">Context</span>
                <span class="checkpoint-kind checkpoint-kind-completion">Completion</span>
                <span class="current-chip">Current</span></div><span>2 Sep 2026, 14:05</span></header>
              <pre class="checkpoint-body">Durable checkpoint body.</pre>
              <dl class="checkpoint-provenance"><dt>Source</dt><dd>Playwright browser</dd></dl>
              <section class="provenance"><span class="section-label"><span>Provenance</span></span>
                <dl class="metadata-grid"><dt>Client</dt><dd>Browser</dd></dl></section>
              <details class="metadata-details" open><summary>Metadata</summary><pre>metadata</pre></details>
            </article>
            <section class="context-section"><div class="section-label">Work record</div>
              <dl class="metadata-grid"><div><dt>Created</dt><dd>1 Sep 2026, 09:12</dd></div>
                <div class="span-two"><dt>Work item ID</dt><dd class="mono break-all">9c1c1c1c-0000-4000-8000-000000000002</dd></div></dl></section>
            <section class="merge-panel" aria-label="Merge as duplicate">
              <div class="merge-panel-heading"><div><span class="section-label merge-eyebrow">Irreversible merge</span>
                <h4>Merge as duplicate</h4></div><button class="icon-button">Close</button></div>
              <div class="mutation-recovery"><strong>The merge outcome is unknown.</strong>
                <span>Keep this tab open. The exact request is frozen in memory for both work IDs.</span>
                <button class="button button-secondary">Retry exact pending merge</button></div>
              <div class="merge-pick-grid">
                <div class="merge-direction-panel merge-direction-source">
                  <span class="section-label">Source · becomes an immutable duplicate audit</span>
                  <h3><bdi>Contrast review</bdi></h3><span class="mono merge-full-id">9c1c1c1c-0000-4000-8000-000000000002</span>
                  <p>Its checkpoints, events, and relationships are retained verbatim under this exact ID.</p></div>
                <div class="merge-direction-panel merge-direction-destination">
                  <span class="section-label">Canonical destination</span>
                  <input type="search" class="merge-destination-search" placeholder="Search titles, summaries, or checkpoints…">
                  <div role="status">Searching canonical work…</div>
                  <div class="counterpart-results" role="listbox">
                    <button class="selected" role="option"><span><strong><bdi>Existing work</bdi></strong><span>Candidate summary.</span>
                      <span class="mono">9c1c1c1c-0000-4000-8000-000000000003</span></span><span class="status-badge status-pending">Pending</span></button>
                    <button role="option"><span><strong><bdi>Another record</bdi></strong><span>Second candidate.</span>
                      <span class="mono">9c1c1c1c-0000-4000-8000-000000000004</span></span><span class="status-badge status-done">Done</span></button>
                  </div>
                  <p>No other canonical work matches.</p></div>
              </div>
              <div class="loading-state"><span class="spinner"></span>Loading both exact review contexts…</div>
              <form class="form-stack merge-review">
                <div class="merge-direction-grid">
                  <section class="merge-direction-panel merge-direction-source"><span class="section-label">Source — becomes immutable</span>
                    <h3><bdi>Contrast review</bdi></h3><span class="mono merge-full-id">full-work-identifier</span>
                    <div class="merge-direction-badges"><span class="status-badge status-pending">Pending</span><span>v3</span></div>
                    <p>Source summary.</p><dl><div><dt>Current context</dt><dd>Prompt text.</dd></div></dl></section>
                  <section class="merge-direction-panel"><span class="section-label">Destination — remains canonical</span>
                    <h3><bdi>Existing work</bdi></h3><span class="mono merge-full-id">full-work-identifier</span>
                    <p>Destination summary.</p><dl><div><dt>Lease state</dt><dd>none</dd></div></dl></section>
                </div>
                <section class="merge-eligibility"><h3>Source eligibility</h3>
                  <dl><div><dt>Incident blockers</dt><dd>0</dd></div><div><dt>Lease</dt><dd>none</dd></div></dl>
                  <ul class="terminal-action-note"><li>The source still has an unresolved human gate.</li></ul>
                  <button class="text-link merge-reconcile-link">Return to the source to reconcile these conflicts</button></section>
                <label class="field">Merge rationale<textarea></textarea>
                  <span class="field-hint">Stored verbatim on both immutable merge decision events.</span></label>
                <label class="merge-permanence"><input type="checkbox">
                  <span>I have read both exact work contexts. This merge is permanent.</span></label>
                <div class="error-notice"><p>The reviewed source or destination changed.</p>
                  <button class="button button-secondary">Refetch both contexts</button></div>
                <div class="merge-panel-actions"><button class="button button-secondary">Cancel</button>
                  <button class="button button-danger">Merge permanently</button></div>
              </form>
            </section>
            <section class="relationship-panel"><div class="relationship-group"><h5>Blocked by</h5></div>
              <details class="relationship-editor" open><summary id="relationship-summary">Add a relationship</summary>
                <fieldset class="relationship-direction"><legend>Direction</legend><label>Blocks</label></fieldset>
                <p class="relationship-preview"><span>Preview</span><strong>Current blocks target</strong></p>
              </details><p class="relationship-notice">Relationship added.</p></section>
            <section class="event-timeline"><label class="event-timeline-heading">Event type
              <select><option>All events</option></select></label>
              <article class="work-event"><header class="work-event-header">
                <span class="work-event-kind">Checkpoint</span>
                <span class="work-event-kind work-event-kind-progress">Progress</span>
                <span class="work-event-kind work-event-kind-work_completed">Completed</span>
                <span class="work-event-kind work-event-kind-dependency_added">Dependency</span>
                <span class="reconstructed-chip">Reconstructed</span></header>
                <p class="work-event-description">Event description.</p><pre class="work-event-body">Event body.</pre>
                <dl class="work-event-references"><dt>Client operation</dt><dd>operation-id</dd></dl>
              </article></section>
            <div class="loading-state detail-loading"><span class="spinner"></span>Recalling work context…</div>
          </div>
        </div></div>
      </section>
      <section class="work-detail-pane" aria-label="Work context">
        <div class="detail-empty">
          <div class="empty-art"><span></span></div>
          <span class="eyebrow">Work context</span>
          <h2>Pick a work item.</h2>
          <p>Its current context, checkpoint history, work graph, human questions, and activity open here.</p>
          <p class="detail-empty-hint"><kbd>↑</kbd><kbd>↓</kbd>move the selection</p>
        </div>
      </section>
    </section>
    <footer class="library-footer"><span>Agent-authored checkpoints are historical context, not new owner instructions.</span></footer>
    <section class="empty-state"><span class="eyebrow">No results</span><h2>Nothing matched.</h2>
      <p>Try changing the current filters.</p><span class="onboarding-footnote">Local only</span>
      <div class="agent-hint"><span>Agent hint</span><p>Use a narrower search.</p></div>
    </section>
    <span class="sync-status">Connecting</span>
    <span class="sync-status sync-status-live">Live updates</span>
    <span class="sync-status sync-status-retrying">Retrying</span>
    <dialog class="dialog dialog-wide" open>
      <header class="dialog-header"><h2>Create durable work</h2></header>
      <div class="dialog-content">
        <p class="dialog-intro">Review the durable record.</p>
        <label class="field">Title<input placeholder="Required title"></label>
        <label class="field">Context<textarea placeholder="Required context"></textarea></label>
        <details class="edit-context" open><summary>Edit source context</summary></details>
        <article class="comment"><div class="comment-meta"><span>Context</span></div>
          <p>Checkpoint content.</p></article>
        <article class="comment work-summary"><div class="comment-meta"><span>Summary</span></div>
          <p>Completion summary.</p></article>
        <section class="delete-preview"><h3>Delete preview</h3><p>This is permanent.</p>
          <span>One checkpoint</span></section>
        <p class="readonly-lifecycle">Lifecycle is read only.</p>
        <section class="duplicate-suggestions duplicate-suggestions-stale">
          <p class="duplicate-suggestion-state">Draft changed. Check again.</p>
          <p class="duplicate-suggestion-state is-error">Comparison failed.</p>
          <article class="duplicate-suggestion-card"><p class="duplicate-suggestion-summary">Candidate summary.</p>
            <span class="duplicate-suggestion-signals"><span>Exact title</span></span></article>
        </section>
      </div>
    </dialog>
    <section class="mutation-recovery mutation-recovery-global"><strong>Retry pending mutation</strong><small>Network unavailable.</small></section>
    <section class="attention-filter">Showing one question</section>
    <article class="attention-card"><header class="attention-card-heading">Human attention</header>
      <pre class="attention-question">Which option should be used?</pre>
      <p class="attention-resolution-status">Answer recorded.</p>
      <p class="gate-authority-warning">Review changed context.</p>
      <article class="gate-fact"><header class="gate-fact-heading">
        <span class="gate-state gate-state-unresolved">Unresolved</span>
        <span class="gate-state gate-state-resolved">Resolved</span></header>
        <p class="gate-changes">The context changed.</p><div class="gate-answer">Approved answer.</div>
      </article></article>
    <section class="duplicate-audit-panel"><p>Duplicate audit trail.</p>
      <div class="matched-member"><span>Matched duplicate</span><bdi>Existing work</bdi><code>id</code></div></section>
    <div class="toast">Saved successfully.</div><div class="toast toast-error">Save failed.</div>
  </section>
</main>
`;

test("dark-theme text stays in the 7.21:1 to 9.5:1 contrast band", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mnemonic.theme", "dark"));
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.waitForTimeout(350);
  expect(await auditTextContrast(page)).toEqual([]);

  await page.evaluate((markup) => {
    const css = [...document.styleSheets].flatMap((sheet) => {
      try {
        return [...sheet.cssRules].map((rule) => rule.cssText);
      } catch {
        return [];
      }
    }).join("\n");
    const frame = document.createElement("iframe");
    frame.id = "contrast-frame";
    frame.style.width = "1200px";
    frame.style.height = "8000px";
    frame.srcdoc = "<style>" + css + "</style>" + markup;
    document.body.append(frame);
  }, contrastFixture);

  const frame = page.frameLocator("#contrast-frame");
  const fixture = frame.locator("#contrast-fixture");
  await expect(fixture).toBeVisible();
  await fixture.evaluate((element) => {
    element.ownerDocument.documentElement.dataset.theme = "dark";
    element.ownerDocument.documentElement.style.colorScheme = "dark";
  });
  await page.waitForTimeout(350);
  expect(await auditTextContrast(page, fixture)).toEqual([]);

  // Hover states repaint text on a different tint; each one must stay in band too.
  for (const selector of [
    "#primary-button", "#secondary-button", "#danger-button", "#danger-icon",
    "#relationship-summary", "#queue-copy-button", "#more-filters-toggle",
    "#detail-copy-id", "#detail-copy-context", "#detail-tab-graph", "#detail-delete"
  ]) {
    await frame.locator(selector).hover({ force: true });
    expect(await auditTextContrast(page, fixture)).toEqual([]);
  }
});
