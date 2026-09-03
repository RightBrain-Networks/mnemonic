import { expect, test } from "@playwright/test";
import { auditTextContrast } from "./text-contrast";

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
    <div class="search-field"><input aria-label="Search" placeholder="Search work items"><kbd>/</kbd></div>
    <button class="semantic-toggle selected">Semantic search</button>
    <button class="filter-button selected">Pending</button>
    <fieldset class="sort-control"><legend>Sort records</legend><div class="sort-options">
      <label class="sort-option selected"><input type="radio"><span>Updated</span></label>
      <label class="sort-option"><input type="radio"><span>Created</span></label>
    </div></fieldset>
    <span class="result-count">3 records</span>
    <article class="work-item-card">
      <div class="card-topline">
        <span class="status-badge status-pending">Pending</span>
        <span class="status-badge status-active">Active</span>
        <span class="status-badge status-dropped">Dropped</span>
        <span class="status-badge status-deferred">Deferred</span>
        <span class="status-badge status-done">Done</span>
        <span class="status-badge status-wont-do">Wont do</span>
        <span class="status-badge status-promoted">Promoted</span>
        <span class="card-source">Source <span>session</span></span>
        <span class="card-version">v3</span>
      </div>
      <button class="card-title"><h2>Contrast review</h2></button>
      <p class="card-summary">A summary of durable work that remains legible.</p>
      <div class="card-footer"><span class="tag">accessibility</span>
        <span class="session-snippet">session <span>abc123</span></span>
        <button class="button copy-button">Copy pointer</button>
        <button class="button defer-button">Defer</button>
      </div>
      <p class="terminal-action-note">Resolve the gate before completing.</p>
    </article>
    <section class="empty-state"><span class="eyebrow">No results</span><h2>Nothing matched.</h2>
      <p>Try changing the current filters.</p><span class="onboarding-footnote">Local only</span>
      <div class="agent-hint"><span>Agent hint</span><p>Use a narrower search.</p></div>
    </section>
    <span class="sync-status">Connecting</span>
    <span class="sync-status sync-status-live">Live updates</span>
    <span class="sync-status sync-status-retrying">Retrying</span>
    <dialog class="dialog" open>
      <header class="dialog-header"><h2>Work context</h2></header>
      <div class="dialog-content">
        <p class="dialog-intro">Review the durable record.</p>
        <p class="detail-summary">A detailed summary must remain readable.</p>
        <label class="field">Title<input placeholder="Required title"></label>
        <label class="field">Context<textarea placeholder="Required context"></textarea></label>
        <details class="edit-context" open><summary>Edit source context</summary></details>
        <pre class="prompt-body">Original prompt text</pre>
        <article class="comment"><div class="comment-meta"><span>Context</span></div>
          <p>Checkpoint content.</p></article>
        <article class="comment work-summary"><div class="comment-meta"><span>Summary</span></div>
          <p>Completion summary.</p></article>
        <section class="provenance"><span class="section-label"><span>Provenance</span></span>
          <dl class="metadata-grid"><dt>Client</dt><dd>Browser</dd></dl></section>
        <details class="metadata-details" open><summary>Metadata</summary><pre>metadata</pre></details>
        <section class="conflict-panel"><h3>Conflict detected</h3><p>Reload before retrying.</p>
          <pre>server value</pre></section>
        <section class="delete-preview"><h3>Delete preview</h3><p>This is permanent.</p>
          <span>One checkpoint</span></section>
        <section class="active-lease-summary"><span class="active-lease-holder">
          <span class="lease-label">Lease holder</span><strong>Agent</strong></span></section>
        <span class="migration-chip">Migrated</span>
        <p class="migration-warning">This record needs review.</p>
        <article class="checkpoint checkpoint-current">
          <header class="checkpoint-header"><span class="checkpoint-kind">Checkpoint</span>
            <span class="checkpoint-kind checkpoint-kind-context">Context</span>
            <span class="checkpoint-kind checkpoint-kind-completion">Completion</span></header>
          <pre class="checkpoint-body">Durable checkpoint body.</pre>
          <dl class="checkpoint-provenance"><dt>Source</dt><dd>Playwright browser</dd></dl>
        </article>
        <p class="readonly-lifecycle">Lifecycle is read only.</p>
      </div>
    </dialog>
    <section class="hierarchy-presentation"><span class="hierarchy-origin">Ancestor path</span>
      <ul class="hierarchy-aggregate-strip"><li>2 pending</li>
        <li class="hierarchy-attention-count">1 blocked</li></ul></section>
    <section class="hierarchy-scaffold"><div class="hierarchy-node-row"><div class="hierarchy-card">
      <article class="work-item-card"><span class="scaffold-label">Ancestor does not match</span>
        <span class="card-source">Inherited source</span><p class="card-summary">Scaffold summary</p>
      </article></div></div></section>
    <nav class="search-ancestry"><span>Parent <span>/</span></span><span>Current work</span></nav>
    <p class="hierarchy-hidden-children">Some descendants are hidden.<small>Change filters.</small></p>
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
    <section class="mutation-recovery"><strong>Retry pending mutation</strong><small>Network unavailable.</small></section>
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
    <section class="merge-direction-panel"><code class="merge-full-id">full-work-identifier</code>
      <p>Destination summary.</p></section>
    <p class="merge-permanence">Merging cannot be undone.</p>
    <section class="duplicate-suggestions duplicate-suggestions-stale">
      <p class="duplicate-suggestion-state">Draft changed. Check again.</p>
      <p class="duplicate-suggestion-state is-error">Comparison failed.</p>
      <article class="duplicate-suggestion-card"><p class="duplicate-suggestion-summary">Candidate summary.</p>
        <span class="duplicate-suggestion-signals"><span>Exact title</span></span></article>
    </section>
    <div class="toast">Saved successfully.</div><div class="toast toast-error">Save failed.</div>
  </section>
</main>
`;

test("dark-theme text stays in the 4.5:1 to 7:1 contrast band", async ({ page }) => {
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

  for (const selector of [
    "#primary-button", "#secondary-button", "#danger-button", "#danger-icon",
    "#relationship-summary"
  ]) {
    await frame.locator(selector).hover({ force: true });
    expect(await auditTextContrast(page, fixture)).toEqual([]);
  }
});
