# Document scope and display rules

Use this reference when an ERP UI change adds purchase, return, reconciliation, or PDF fields, or changes generated document numbers.

## Ledger scope decision table

| Document | Counterparty | Direction | Customer reconciliation | Supplier reconciliation |
|---|---|---|---|---|
| Sales order | Customer | Sale | Include | Exclude |
| Customer return | Customer | Return/credit | Include | Exclude |
| Purchase/inbound order | Supplier | Inbound/payable | Exclude | Include |
| Supplier return | Supplier | Return/debit | Exclude | Include |

Do not infer scope from a similar-looking label. Trace the document's counterparty, posting direction, and existing totals before merging it into a statement.

The table is the default scope. When the user explicitly directs merging a supplier-side document (purchase / supplier return) into a customer view, comply, but keep the direction signs (a purchase reduces what the customer-side ledger shows as owed; a supplier return increases it), update the balance/summary card copy so a netted receivable-plus-payable figure is not read as pure customer debt, and re-run the balance tests. A merged view is a decided exception, not the default, so state the semantics change back to the user.

## No cost, no stock (schema v15)

- Since schema v15 (2026-10-16) this product has **no cost, no stock, and no opening inventory**: the three inventory tables (`inventory_postings` / `inventory_initializations` / `product_inventory_state`) and the cost / safety-stock / opening columns are **physically deleted**.
- Do not add a cost column, a margin figure, a stock value, or an opening-quantity / opening-cost import column. A file carrying an opening quantity/cost column is rejected as a header error, and a page that renders such a figure is showing a value that is no longer stored anywhere.
- Customer-facing statements/PDFs expose only business fields (index, name, unit, quantity, unit price, amount). Internal cost and margin are not merely hidden — they do not exist.

## Entry-line column authority

- The four document types share one column matrix — index / product / unit / quantity / unit price / amount / action. A page that omits a column looks broken next to its siblings.
- The unit starts as a read-only mirror of the product master. Before making it editable, decide in writing whether the edited unit writes back to the master and where a cross-document "remembered" unit is sourced from (the **transaction detail tables**, not the master), rather than inheriting an old rationale.
- A manual-entry price must not default to some catalog figure with no clear owner; leave it blank and let the operator type it.

## Price resolution by document side

The unit-price field means different things on the two sides, and a line's price must be resolved by which side owns it:

| Entry page | Unit price means | Default / quick-fill source |
|---|---|---|
| Customer-side (sale, customer return) | the price this customer pays | that customer's learned price when one exists, else **blank**; re-resolved whenever the counterparty changes, and a hand-entered value > 0 is learned as that customer's price |
| Supplier-side (purchase, supplier return) | the price agreed with the supplier | no default; entered by hand |

- **Never pass a supplier id into the customer-price endpoint.** Customer and supplier records commonly share one counterparty table, so a supplier id is a perfectly valid customer id: the join succeeds, a price comes back, and the page labels it "customer price" when it actually means *what this party once paid as a customer*. Nothing errors — the inbound price field simply gets a semantically wrong candidate. When a supplier-side page needs a reference figure, read it from the catalog the page already embeds (and widen both the query and its serializer), not from the sibling side's endpoint. Check the **parameter name** of any endpoint you borrow for another document type: a shared name (`customer_id`) is not a shared meaning, and one page's header field is often a different entity than the other's despite identical markup.
- **Widening a shared catalog serializer breaks every caller that hand-rolled its own row shape.** When you add a column, grep for the serializer's name across routes *and* tests: sibling endpoints frequently duplicate the query inline, so a `SELECT` list without the new column raises `IndexError: No item with that key` only when that route is hit — a page test that never fetches it stays green.
- **Render nullable columns into form values with `or ''`, never `x if item else ''`.** A guard on the *row object* still yields the row's NULL field, and the template engine stringifies it into `value="None"`. Front-end truthiness then treats that as populated, so a record is silently reclassified by a marker it never had, and re-submitting the form feeds `None` into an `int()` conversion downstream. Assert the served edit page contains `value=""` for a row whose column is genuinely NULL — checking the template source, or the create page where the field is empty by construction, both pass while the bug ships.

## Price provenance: what a price table can and cannot tell you

Before designing a display or aggregate over prices (a per-counterparty price list on a master-data page, an "average selling price" figure, a label claiming where a price came from), enumerate the **write** paths. The page that shows a value is not evidence of who sets it.

- `grep -rn "<price table>" app/ --include=*.py` and read every write site's guard, then grep the master record for its own writers. Report what you find as facts, not as intent.
- Typical shape in this family of systems: one per-counterparty price table keyed `UNIQUE(counterparty, product)` holding only `price_cents` + `updated_at`. Two paths write it — the manual price editor, and the automatic learning done while saving a sale (a hand-entered price that differs from the previous one is written back). The learning path upserts on the unique key, so the table holds **one row per pair: no history, no origin**.
- Purchase documents carry a **price**, never a cost; there is no cost column anywhere in this product. Verify that before treating "the price this product was bought at" and "the price a counterparty pays" as competing sources — they are the same kind of field on different sides of the ledger.
- **A distinction the user asks for may not be representable; price the migration instead of promising it.** "Was this price set by hand or learned from a document?" cannot be answered by a table that stores no origin — both writers share one column. Either derive it from a fact that does exist, or add an origin column with an explicit schema version bump plus migration, and say which one you are proposing. Never plan silently on a distinction the data cannot support.
- **An average over per-counterparty prices is an unweighted arithmetic mean unless you say otherwise.** That table stores no quantity, so a volume-weighted figure needs a join back to document lines and a different口径. Show the sample count beside the mean (`均价 ¥X · N 个客户`) so the basis is visible, and keep "no data at all" distinct from "no learned price yet" — the second looks like a learned price if it carries no label.
- Because the learned row is overwritten by every later transaction, any per-counterparty figure shown is "the most recent agreed price", not an average of that counterparty's history. Put that in the surface's caption rather than letting the user infer a history that is not stored.

## Table and number rules

- Keep status, quantity, unit price, and amount in separate columns when users compare them across rows.
- Preserve numeric precision in storage; choose display precision from the unit's business semantics.
- Display count units as integers when fractional quantities are not meaningful; retain fractional display for measure units that require it.
- Display currency with two decimal places.
- Never use ellipsis to hide a currency amount or other decision-critical numeric value; widen the column, allow responsive layout, or provide a full-value detail surface.
- Give destructive buttons a minimum width and `white-space: nowrap`; responsive wrapping may move the action group, but must not stack a button label vertically.

## Ledger filter facets

- Offer a document-type filter only for types the ledger query actually returns; verify the service includes them first, or the option is dead.
- When a new document type feeds the running balance, add its sign, badge color, ledger column, and PDF projection together in the same slice.
- **A document-level money column must take its sign from the ledger's signed effect, not from the stored total.** Stored totals for every document type are usually positive; only the ledger projection carries direction (a purchase reduces what is owed, a supplier return increases it). Rendering the stored total with a generic `+` prints a purchase as money received. Read the sign from the same field the balance is computed from, and check each direction against a real row of that type — a sale and a return can look right while the purchase sign is inverted. A type whose sign happens to match its stored value (e.g. a supplier return) will not reveal the bug, so test every direction explicitly.

## Generated document numbers

- A generated number is `<prefix><YYYYMMDD><4-digit daily sequence>`, and the sequence is scoped by **date plus prefix**. Before changing a type's prefix, list every prefix already in use in that date scope: the prefix the user asks for may already belong to another document type, and sharing a prefix without sharing the sequence produces duplicate numbers on the same day.
- When the requested prefix collides, do not silently comply and do not silently substitute. Offer the aligned option first — the two related types **share one daily sequence**, as sale and return already share `MD` — and name the concrete files that must change so the user can decide.
- Check whether the other type's allocator also **reserves** sequence numbers (a reserved-sequence table), because reservations are invisible to the new allocator's `LIKE` scan and widen the collision window.
- A prefix change applies to newly generated numbers only. Never rewrite the numbers of existing documents: the number is a historical fact already referenced by PDFs, links, and reconciliation snapshots.
- Move every place that formats the prefix together: the next-number allocator, the service that allocates inside the write transaction, the reserved-sequence table, the form's helper text, and the tests asserting a sample number.
