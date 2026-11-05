---
name: erp-ui-extension
description: Use when extending accepted ERP UI; preserve workflows.
version: 1.2.0
author: Project
license: MIT
platforms: [windows, linux]
metadata:
  hermes:
    tags: [erp, ui, ux, prototype, regression, workflow]
    related_skills: [agent-development-discipline]
---

# ERP UI Extension

## When to Use

Use when adding purchasing, returns, analytics, reconciliation, or other workflows to an existing ERP whose UI/UX has already been implemented or accepted.

## Procedure

1. **Freeze the baseline before designing.** Read the current PRD, real templates, shared layout/styles, existing interaction tests, and previously accepted prototypes for affected pages. Map navigation, page ownership, surfaces, controls, responsive rules, keyboard behavior, filters, pagination, and print/preview boundaries. When continuing a dirty cross-session worktree, inspect live source, tests, and uncommitted files before trusting the plan's phase labels; preserve unrelated partial slices and reconcile the plan only after fresh evidence. Reconcile the index and status lines as well as the plan body: an entry still saying a completed item was out of scope, or quoting a stale evidence count, misleads the next session into redoing or skipping work. Re-verify the count you are about to write by running the suite, never by copying the previous line.
   - Details for steps 1-3 live in [`references/requirement-investigation.md`](references/requirement-investigation.md): batched-feedback triage, regression vs never-implemented vs order-conditional, merge/split scoping, picker bootstrapping, and remember-value contracts.
2. **Define extension points, not a replacement product.** Map each new workflow to an existing owner such as workbench, order entry, document management, master data, analytics, or accounts. Reuse the existing shell, topbar, sidebar hierarchy, glass panels, table density, button language, theme controls, and viewport controls.
   - 2a (deleting a load-bearing control) is in `references/requirement-investigation.md`.
3. **Preserve accepted interactions explicitly.** Carry forward customer/product autocomplete with ArrowUp/ArrowDown/Enter/Escape, customer-specific price carry-over (blank when that customer has no learned price), one default line plus add-row and clear-all, date-mode filters, 50-row pagination, selection semantics, same-day history, and the existing workbench quick-action layout.
   - 3a-3c (picker bootstrapping, remember-value contracts) are in `references/requirement-investigation.md`.
4. **Design the vertical slice in the real task order.** For this ERP, show: existing entry point → master data/product identity → purchase/inbound entry → sale/return entry → analytics → reconciliation/print. Each screen must expose the next meaningful state, not only a final result.
   - 4a-4i (cross-page conventions, precision, theming, sibling document types, consolidated lists, source preview) are in [`references/extension-design-patterns.md`](references/extension-design-patterns.md).
5. **Separate internal and external views in the prototype.** Internal forms may show internal notes and source references. Customer print/reconciliation previews must use an allowlisted field set and show that internal fields are excluded.
6. **Version rejected prototypes instead of overwriting them.** Preserve the rejected file, create a clearly versioned replacement, update the asset record, and point the plan/evidence document at the replacement as the acceptance target.
7. **Build each vertical slice red-to-green.** Add the smallest behavior tests first, implement the service/data rule, connect the existing route/template path when the slice is user-visible, run targeted tests, then run the regression suite before starting the next slice. Do not stop at a schema or service stub. For master-data slices, advance the schema version explicitly, test migration-safe field creation, and verify the complete chain of service, route, template, backup/migration, and purge/restore behavior before marking the phase passed.
8. **Test idempotency through the route, not only the service.** When a form allocates a document number or other generated identifier, look up an existing request key before allocating a new identifier; otherwise a retry changes the payload and becomes a false conflict instead of returning the original result.
9. **Audit lifecycle edges for every master record.** When a product or customer is soft-deleted, inspect and test restore, permanent purge, and automatic cleanup in the same slice: a product still referenced by a live document cannot be physically deleted, and a customer with any accounting history is never physically deleted.
10. **Verify in layers before product acceptance.** Run JavaScript syntax, duplicate-ID, required-control, `git diff --check`, and HTTP-served load checks first. **A server-rendered template's inline JS is invisible to a linter, so extract it from the rendered HTML and check it — a helper you called but never defined renders fine, passes every template test, and dies at runtime.** A template test asserting `class="unit-cell"` or `name="unit"` is present says nothing about whether the handler can run. Recipe: render each affected page through the test client, regex out every `<script>` block **without** a `src` attribute, join them, and run `node --check` on the concatenated file per page;
then grep each page's own JS for every helper name it calls and confirm exactly one `function <name>` definition exists in *that* page's output. Hold the same bar for the reference count, not just existence: a page that calls `ensureUnitOption(...)` twice and defines it once is correct, one that defines it zero times is the bug. Do this before the browser pass — the browser would surface it only as 'the dropdown does nothing', which is far slower to localise. Then attempt real browser interactions for primary paths and responsive/theme checks. Lower-layer success never upgrades a higher layer. When scripting a native two-level picker, choose options by label or explicit value: selecting the first option index picks the placeholder and silently clears the bound id, which later surfaces as a misleading server-side validation error. Before handing the user a preview URL,
confirm the instance holds data rich enough to exhibit the change and name that dataset in the handoff — a preview seeded with one document renders a correct new layout as empty or degenerate and reads to the user as a broken feature. **When the change makes a destructive operation reachable for the first time (permanent purge, empty-the-bin, bulk delete), copy the database before handing over the URL and tell the user the backup path and that you will restore it.** Until now those actions may have been impossible or reversible, so the user previews freely; once they actually delete, an un-backed-up preview costs real data. Also confirm the data root the instance serves and say which one it is, so "I previewed and it's fine" refers to the copy rather than the live database. Serve the richer dataset through an explicit data-root/config override (an env var such as `ERP_DATA_ROOT` plus a config path) pointed at a copied database, and leave the user's real data root and its location file untouched;
then confirm the override took effect by reading the root back from the app's own health/status endpoint rather than assuming it. **A fresh scratch config path must be seeded before the app factory runs** — pointing the config env var at a directory that has no config file yet aborts startup with a file-not-found, so write the shipped defaults into that path (and record the scratch data root) *before* calling the app factory, not after. Opening an older packaged database can silently upgrade its schema on first write, so copy it into a scratch data root first and never point the override at the user's live database. Also confirm *which* database the instance actually reads before describing its contents: a repo-local database file can be stale or unused while the effective root lives elsewhere, so read the resolved root from the app instead of inferring it from a file you found in the tree. **When the user's screenshot must be explained, identify the instance behind it from the app, not from the filesystem.** List the listening ports, hit each candidate's health/status endpoint (these typically return the resolved data root, database status, and version),
and compare that instance's rows against what the screenshot shows. Walking the disk for database files is the slow path and often finds nothing that matches: a scratch preview instance seeded with synthetic data (a demo customer, a handful of documents) produces a screenshot that corresponds to no database in the repo tree, and its data root usually sits under the scratch/cache directory rather than in the project. Name the data root you concluded from in the answer, so "why does this row show X" is answered about the database the user was actually looking at. Confirm the serving process **postdates your last code change**: a long-running dev instance with template caching on keeps serving the pre-change HTML, and a user who closed the browser window has not stopped the service. Note the split that makes this trap hard to see: **static JS is re-read from disk per request while templates — and the inline CSS inside them — are cached**, so a JS-only edit takes effect immediately and a CSS/template edit does not. Restart the instance before re-verifying markup or styling, and never infer from "the JS change is live" that the whole edit is:
an unchanged popup width or column width after you edited CSS is this trap, not a specificity bug. Check the port's listener and whether the page it returns actually contains the new markup rather than trusting that the port is free or a report that it was closed; when the running instance is stale, start a fresh one on a different port against the copied data root instead of reusing it. **When you hand a URL to the user for their own verification, the same staleness bites the other way: name the exact URL and warn that any other instance they open — an older port, their usual shortcut/launcher, the packaged EXE — serves pre-change markup.** A user who clicks their normal entry point instead of your URL verifies the old build and reports a correct change as "not applied"; when that happens, resolve it by comparing the served bytes on the URL you gave against what they are looking at, and ask for their address bar, rather than re-editing correct code or telling them they misread.
11. **Report the gate honestly.** Mark each layer `passed`, `failed`, `blocked`, or `unverified`. Keep the phase gate open when real interaction or visual review did not happen. An explicit instruction to continue may authorize the next plan-approved isolated implementation slice, but it does not convert an unverified prototype or manual review into acceptance. Before attributing a test failure to the product, reproduce it with the project's documented default invocation: a failure that appears only under an extra flag is an invocation artifact, because flags can disable plugins a test depends on (e.g. a no-logging flag removes the built-in log-capture fixture, so a green suite reports a setup error). **A bare "N passed, N skipped" from a backgrounded suite is not an evidence count — check the skip reason before recording it.** Some test modules gate on a binary resolved through `shutil.which`, and a background/detached run can start with a PATH missing that tool (a bundled `pdftotext`, `node`, a packager), so the file skips wholesale while the same command in the foreground runs every test. Treat any nonzero skip count as suspect:
re-run in the foreground (or the project's documented default invocation here) and confirm `0 skipped` before quoting the number. Re-run that single test under the default command and quote the exact invocation beside any count you report — a bare "N passed, 1 error" that came from a non-default flag misrepresents the product.

## Always-On Rules

- Existing accepted UI/UX is a compatibility contract; do not replace it with a fresh conceptual dashboard.
- Read real templates and accepted prototypes before writing a new prototype; screenshots or memory are insufficient.
- Prefer extending real controls and states over explanatory prose, static metric cards, or buttons that only emit prototype toasts.
- Preserve old and new business meanings separately; label new net-sales analytics instead of silently changing old purchase-statistics semantics. When you repurpose an existing visualization or aggregate onto a different metric, rename its label, keep the previous metric still computed so the change stays auditable, and confirm the sign convention and which document types are in scope before implementing — a bare word like "flow" is ambiguous between gross turnover and direction-netted movement.
- Treat keyboard flows, filter modes, pagination, selection, same-day history, theme, and viewport behavior as first-class UX.
- Do not claim acceptance because a prototype loads or parses; acceptance needs real interaction evidence or an explicit user decision. A continuation instruction can authorize implementation behind an unverified prototype gate only when the plan explicitly permits that separation; keep the gate labeled `unverified`.
- Treat live-document references as an ownership boundary: soft-delete hides a product, while purge/cleanup must refuse a product still referenced by a live document; a customer with accounting history is never physically deleted.
- Add a filter facet only when the backing data actually reaches the view: confirm the query/ledger already returns those document types before adding the option, or the options are permanently empty and the real change is a scope/口径 change, not a UI filter.
- When the user rejects a direction for missing baseline UX, acknowledge the root cause and rebuild from the baseline rather than defending the abstraction.

Everything else lives in the topic references below; read the one that matches the change before acting:

- [`references/rendering-and-layout-measurement.md`](references/rendering-and-layout-measurement.md) — read before claiming a layout, size, depth, or legibility requirement is met, and before measuring any geometry.
- [`references/filter-list-table-interaction.md`](references/filter-list-table-interaction.md) — read before touching a filter card, a document list, a table action column, an in-place row expansion, or a typeable select.
- [`references/state-and-data-consistency.md`](references/state-and-data-consistency.md) — read before deriving a value from another field, adding a status flag, inserting a synthetic row, or surfacing a rejected action.
- [`references/acceptance-and-evidence.md`](references/acceptance-and-evidence.md) — read before reporting a defect, merging or renaming a section, bumping an asset token, or declaring a requirement met.

## References

### Procedure and delivery

- [`references/baseline-extension-checklist.md`](references/baseline-extension-checklist.md) — before creating a new page or extending an accepted one.
- [`references/document-scope-and-display-rules.md`](references/document-scope-and-display-rules.md) — when deciding what a document type shows.
- [`references/requirement-investigation.md`](references/requirement-investigation.md) — before turning UI feedback into findings or scoping a field merge/split.
- [`references/extension-design-patterns.md`](references/extension-design-patterns.md) — before designing a vertical slice or extending an accepted entry/list UI.
- [`references/document-list-and-cross-document-links.md`](references/document-list-and-cross-document-links.md) — before changing a list page or a cross-document link.
- [`references/import-and-completion-flags.md`](references/import-and-completion-flags.md) — before touching import or the completion flags.

### Rendering and interaction

- [`references/template-styling-and-theming.md`](references/template-styling-and-theming.md) — before restyling or re-theming any template.
- [`references/layout-parity-and-row-fitting.md`](references/layout-parity-and-row-fitting.md) — before changing a page layout or fitting a row.
- [`references/browser-audit-recipes.md`](references/browser-audit-recipes.md) — before verifying a layout/theme fix in a real browser.
- [`references/in-place-fragment-refresh.md`](references/in-place-fragment-refresh.md) — before wiring a fragment refresh or an in-place update.
- [`references/autocomplete-suggestion-reliability.md`](references/autocomplete-suggestion-reliability.md) — before changing an autocomplete or suggestion list.
- [`references/rendering-and-layout-measurement.md`](references/rendering-and-layout-measurement.md) — before claiming a layout, size, depth, or legibility requirement is met.
- [`references/filter-list-table-interaction.md`](references/filter-list-table-interaction.md) — before touching a filter card, list page, table action column, row expansion, or typeable select.

### State, failure and acceptance

- [`references/state-and-data-consistency.md`](references/state-and-data-consistency.md) — before deriving a value from another field, adding a status flag, or inserting a synthetic row.
- [`references/action-failure-surfacing.md`](references/action-failure-surfacing.md) — before wiring up any action that can fail.
- [`references/acceptance-and-evidence.md`](references/acceptance-and-evidence.md) — before reporting a defect or declaring a requirement met.
- [`references/document-print-pagination.md`](references/document-print-pagination.md) — before making a long document print or paginate.

### Audit scripts

- Run [`scripts/audit-nav-and-scroll.mjs`](scripts/audit-nav-and-scroll.mjs) (`node audit-nav-and-scroll.mjs <url> [cdp-port]`) to check nav/scroll behaviour on a live page.
- Run [`scripts/audit-control-centering.mjs`](scripts/audit-control-centering.mjs) (`node audit-control-centering.mjs <url> [cdp-port]`) to measure control centering.
- Run [`scripts/audit-glass-depth.mjs`](scripts/audit-glass-depth.mjs) (`node audit-glass-depth.mjs <url> [cdp-port]`) to measure panel depth/stacking.
