# Baseline Extension Checklist

Use this before reviewing a prototype that extends an accepted ERP UI.

## Baseline inventory

- [ ] Shared shell and theme tokens read
- [ ] Real route/template for every affected page read
- [ ] Previously accepted prototypes read
- [ ] Existing behavior tests located
- [ ] Navigation and page ownership mapped
- [ ] Print/PDF and internal/external data boundaries mapped

## Interaction carry-forward

- [ ] Customer autocomplete: input, ArrowUp, ArrowDown, Enter, Escape, blur recovery
- [ ] Product autocomplete: concrete product identity, unit, customer price
- [ ] Order entry: one initial line, add row, clear row, clear all, live totals
- [ ] Date filters: year/month/range modes and stable export actions
- [ ] Lists: 50-row pagination, current-page selection, selected-count feedback
- [ ] Same-day history uses business date, not created/updated timestamp
- [ ] Theme and viewport controls remain available

## Extension checks

- [ ] New workflow attaches to an existing owner or is justified as genuinely new
- [ ] Old semantics remain labeled and preserved when new semantics differ
- [ ] Internal fields are visibly separated from customer-facing preview fields
- [ ] Empty, loading, invalid, negative, and disabled states are represented
- [ ] Primary path crosses the real task sequence, not only isolated screens

## Verification order

1. JavaScript syntax check.
2. Duplicate-ID and required-control check.
3. `git diff --check`.
4. HTTP-served load and asset request.
5. Browser interaction checks for navigation, autocomplete, filters, pagination, multi-row entry, selection, theme, and viewport.
6. Visual, console, and network review.

A prototype is `unverified` at the interaction or visual layer when the browser session cannot be established; do not upgrade it based on HTTP status, parsing, file existence, or asset registration alone.
