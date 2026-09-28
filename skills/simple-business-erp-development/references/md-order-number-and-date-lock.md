# MD order number + date rules (simple ERP)

Updated 2026-07-12 after user corrections.

## Format

- Stored: `MD` + **`YYYYMMDD` (4-digit year)** + `0001`… (4 digits)
- Example: `MD202607120001`
- No `/` in the string (user’s `MDyy/mm/dd0001` is documentation only)
- **Do not** use 2-digit year (`MD2607120001` was rejected: 「26改为2026」)

## Rules

| Action | order_date | order_no |
|--------|------------|----------|
| New sale/return (default) | system today | `next_order_no(conn, today)` |
| New sale/return (operator picks date) | chosen valid ISO date | `next_order_no(conn, that date)` |
| Re-edit | keep DB value (readonly) | keep DB value (readonly) |

- Sale + return **share** the sequence for that **business day**.
- New UI: date **editable**; order number **readonly**; changing date refreshes number via `GET /orders/api/next_order_no?date=…`.
- Backend create: trust valid form `order_date`; **ignore** form `order_no`.
- Backend update: `SELECT order_no, order_date` then pass those into update service—ignore form tampering.
- Old non-MD / short-year numbers: leave unchanged on re-edit.

## Implementation touchpoints

- `app/erp/routes/orders.py`: `next_order_no`, `_resolve_create_order_date`, `_create_order_view`, `api_next_order_no`, `update_order_view`
- `app/erp/templates/orders/new.html`: `#orderDateInput` (editable on create), `#orderNoInput` (readonly), JS refresh on date change
- Tests: `tests/test_order_numbering.py` (selected-date create, API preview, re-edit lock), async save-print / print / return tests must assert by selected date or customer_id, not by a client-supplied fake order_no

## Pitfalls

- Tests that POST a custom `order_no` and look it up by that string will fail—query by `customer_id` / returned payload instead.
- Freezing create date to “always today, readonly” was wrong for real shop use; default today + editable date + number follows date is the confirmed behavior. User later corrected the first “always today” implementation—do not reintroduce create-date readonly.
- Two-digit year was wrong; always 4-digit year in the MD body (`MD202607120001`, not `MD2607120001`).
- Re-edit must still lock both date and number even after create-date became editable.
