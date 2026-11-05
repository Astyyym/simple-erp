# In-place fragment refresh (no flash, no jump to top)

Use when wiring a filter submit, a pagination link, or an in-page view switch so it
updates results WITHOUT a full navigation — the fix for "点查询后页面闪烁刷新并回到最上方".

Root cause and the measurement that proves it live in
[`browser-audit-recipes.md`](browser-audit-recipes.md) → "Scroll position
across a filter submit". This file is the implementation recipe.

## Shape

1. **Extract one shared module** (`static/page-refresh.mjs`) rather than adding a third
   copy of the logic. Export the four pieces: `scrollAnchor(doc)`, `refreshInPlace(root,
   url, doc, fetchImpl)`, `formUrl(form)`, `wireInPlace(root, doc)`.
2. **Mark the result regions in the template** with a fragment attribute plus a stable
   `id` — e.g. `<section id="ordersResults" data-refresh-fragment>`. The swap is keyed by
   id: for each fragment in the fresh response, replace the same-id node in the live DOM.
   **Every state of the same region must reuse that one id** — the "nothing selected yet"
   empty state and the populated result state are the same region, so both branches render
   the same `id` and both carry the fragment attribute. Giving the empty state its own id is
   what breaks the first submit (pitfall 8).
3. **Mark the controls** that should refresh in place with `data-inplace` on the `<form>`
   and on the pagination/facet `<a>` links. One attribute, one delegated listener.
4. **Delegate on `document`, not on the elements.** This is the load-bearing decision
   (see pitfall 1).
5. **On success**: swap fragments → `history.pushState` the new URL → restore the scroll
   anchor → dispatch a `erp:refreshed` CustomEvent so page scripts can re-derive state.
6. **On any failure**: fall back to `location.assign(url)`. Never leave the user on a
   half-updated page.

## Pitfalls (each cost real debugging time)

1. **Any JS bound directly to an element inside a fragment dies on the first refresh.**
   The old node is replaced, so its listeners go with it — the control silently stops
   working after one use. Rewrite that page's script to delegate from `document`
   (`event.target.closest(...)`), and read values off the *current* DOM each time rather
   than capturing them at load. A page that mixes a fragment swap with element-bound
   handlers is the classic "it worked once, then did nothing" report.
2. **Never fire a submit with `form.submit()` from an inline `onchange` when the form
   should be intercepted.** `HTMLFormElement.submit()` bypasses the `submit` event
   entirely, so a delegated `submit` listener never sees it and the page does a full
   navigation. Use `this.form.requestSubmit()` (fires a cancelable `submit` event) — the
   delegated interceptor then catches it. This is the difference between "勾选类型后页面
   还是闪" and a working in-place toggle.
3. **A `<script type="module">` is deferred; an inline classic `<script>` in the body is
   not.** So a page's inline script runs BEFORE the module that defines the helper. Do not
   expect the page script to call `window.erpHelper.init()`. Have the module auto-init on
   its own load (`if (document.readyState === 'loading') addEventListener('DOMContentLoaded', boot); else boot();`),
   and gate it on the presence of the marker attribute so pages with no such controls pay
   nothing.
4. **Nodes inserted by `importNode`/`replaceWith` do not execute their `<script>` tags.**
   Keep any fragment-local initialization in the shared module's delegated listeners or in
   a `erp:refreshed` handler — never as an inline script inside the fragment.
5. **Handle `popstate`.** After `pushState`, a browser Back must re-fetch and swap the
   fragments for the restored URL, not leave the DOM showing the newer state. Fall back to
   `location.reload()` if the fetch fails.
6. **Re-derive any "has the user changed the form" baseline on `erp:refreshed`.** A page
   that compares current field values against a baseline captured at load will see its
   freshly-swapped fields as "changed" and start warning the user about unsaved edits.
   Re-sample the baseline in the refresh handler.
7. **Keep the fetch header on the request** (`X-Requested-With: fetch`) if the server
   branches on it, and keep any client script that syncs export/preview links to the live
   form values — a swap must not leave those pointing at stale parameters.
8. **A fragment that exists only in the fresh response is dropped silently — so a region
   whose id varies by state never appears on the first submit.** The module iterates the
   fresh document's fragments and looks each id up in the live DOM; when the live page has no
   node with that id it neither inserts the new one nor raises (the replaced-count is not
   checked), while `pushState` has already written the new URL. The user sees 「点了查询没反应」
   with a correct-looking address bar, and a manual refresh then shows the data. The shape is
   a results region written as `{% if selected %}<section id="results" …>{% else %}<section
   id="empty" …>{% endif %}`: the empty→populated transition is the *first* submit and it
   never swaps. Diagnose by reading `document.querySelectorAll('[data-refresh-fragment]').length`
   before and after the submit — 0 → 0 while the URL changed is the signature — and by
   confirming the fetch really returned 200, because a 200 whose fragments were all dropped
   looks identical to a failed request. Fix by giving the empty state the **same id** as the
   result state (step 2). The sibling variant is easier to miss: a branch that renders *no*
   fragment at all (a bare empty/error page) makes the fresh document fragment-less, so
   `refreshInPlace` throws and the caller's `location.assign` fallback silently rescues it —
   that path works, which is why the different-id variant reads as a mystery.
9. **The same drop hits a container whose *presence* is conditional on having content.** A
   selected-conditions chip row written as `{% if chips %}<div class="filter-chips"
   data-refresh-fragment id="chips">…</div>{% endif %}` is exactly the live-page-has-no-such-id
   case — and the first filter submit is precisely when it should appear, so the user filters,
   sees no chip, and has no one-click way to remove the condition. Render the container
   **always** and hide the empty state with a class (`.filter-chips.is-empty{display:none}`),
   **not** with the `hidden` attribute — a `display:flex` rule already on the container outranks
   `[hidden]`. Assert the container exists in both states and drive the empty→non-empty
   transition, not just the populated→populated one. Any region that can go from nothing to
   something (a chip row, a warning banner, a results table) needs this treatment; the id
   alone is not enough when the node itself is absent.

## Verifying it

- Set the pane's `scrollTop` non-zero, click the primary submit, wait, read `scrollTop`
  back: it must be unchanged, and `performance.getEntriesByType('navigation').length`
  must NOT have increased (that count is the proof no full navigation happened).
- Confirm the fragment actually swapped (row count or a value that changes with the
  filter), not just that the URL changed. **Drive both directions of every state
  transition** — empty→populated, populated→populated (switch the selected record), and
  populated→empty — because the empty→populated first submit is the one that silently
  no-ops, and a check that only ever re-queries from an already-populated page passes right
  over it. When a transition fails, verify it against a pre-change instance before calling it
  your regression (see `acceptance-and-evidence.md` in this skill).
- Unit-test the module with a stubbed `document`/`DOMParser`/`fetch`: assert it swaps
  matching ids, drops stale fragments, restores the anchor, and rejects (so the caller
  can fall back) when the response is non-ok or contains no fragment.
