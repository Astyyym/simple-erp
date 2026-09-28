# Archived full SKILL body (pre-thin)

> 历史技能归档，不是当前规则来源。登录门、路径、打印方案和产品状态均可能过时；当前产品以 `AGENTS.md`、项目主技能和 `docs/需求/` 为准。旧凭据已移除，不要从本归档恢复。


# Small-business ERP development

Use when building or modifying a lightweight local ERP/order-entry app for the user, especially Flask + SQLite + HTML templates.

## User workflow (mandatory — 简单ERP)

Confirmed 2026-07-12. Do **not** skip or reorder unless the user explicitly overrides:

1. **User states a requirement**
2. **Agent restates understanding + asks remaining questions** (no code yet if material ambiguity remains)
3. **User answers; confirm no open questions**
4. **Write/update 开发短计划** under project `开发短计划/`
5. **Implement against the short plan** (tests + Windows/dev gate as usual)
6. **Only after user says testing is OK** → version docs → commit/push → rebuild EXE overwrite local `dist/` → ZIP → **GitHub Release upload** of installers

Never auto-commit/push/bump/EXE/Release mid-feature. User will explicitly ask to “更新到本地 / 推 GitHub / Release 装包”.

**Development/preview environment:** the authoritative checkout is Windows `本地 Windows 仓库根目录`. Windows is the default for development, desktop flows, packaging, and acceptance. WSL may access the same checkout through `WSL 中映射的同一仓库` for optional cross-tests and a `5001` preview; it is not a second repository and is not the mandatory preview after every change. When a WSL preview is actually needed, the agent owns the process and sends only a URL that has passed a Windows-side marker probe. Windows source/EXE remains on `5000`. Details: `references/agent-managed-dev-preview.md`.

## Core rules

1. Preserve business history. Use soft delete/recycle bin for orders/products/customers; only purge after checking foreign keys and snapshot fields.
2. Money is integer cents in storage; only format yuan at UI/PDF boundaries.
3. Tables used for deletion must be visually stable: fixed table layout, fixed operation-column width, fixed row height, and avoid page reload for single-row delete when possible.
4. Do not nest `<form>` elements. For bulk actions, use a standalone form plus checkbox `form="formId"`; keep each row action form separate.
5. User-facing errors must not become Flask 500 pages for common business cases: duplicate order numbers, foreign-key protected deletes, invalid money input.
6. Verify with tests after changes; add regression tests for every bug the user reports.

## Login gate (single account; creds updated 2026-07-14)

User simplified mid-stream from multi-user/register/≤5 accounts: **one default username/password, same feature train** (not deferred). When he says「现在就做 / 先不要那么多复杂的」after a long Q list, implement the simplified scope immediately—do not re-ask the multi-user matrix.

- Defaults (historical login gate, superseded by v0.8.0 permanent no-login): credentials intentionally omitted; do not restore them from this archive.
- Config reuse: `local_access_username`, `local_access_password_enabled=true`, `local_access_password_hash` (werkzeug). Empty hash → bootstrap default on `create_app` via `ensure_auth_defaults`. Do not invent a parallel password system.
- Changing the account: update `DEFAULT_USERNAME`/`DEFAULT_PASSWORD` in `app/erp/auth.py`, `CONFIG_DEFAULTS.local_access_username` in `config.py`, live `config.json` (+ EXE `_internal/config.json` if present), README; regenerate hash with `generate_password_hash`. Old credentials must fail after the change.
- Gate: `before_request` redirects unauthenticated HTML to `/login`; public: `/login`, `/logout`, `/health`, static.
- Session + topbar user + 退出登录.
- Login UX: password field **show/hide eye toggle** (`#togglePassword` on `auth/login.html`); keep it when restyling login.
- Tests: autouse `ERP_DISABLE_AUTH=1` in conftest; dedicated `tests/test_auth_login.py` clears that env. Also clear `ERP_DESKTOP` / `ERP_DESKTOP_SHELL` in conftest so order-dependent tests do not leak `data-desktop="1"`.
- Request-context pitfall: `current_username()` must not crash outside request (PDF/CLI). Wrap session access or WeasyPrint template context fails with Working outside of request context.
- Do not re-open multi-account/register/≤5 users unless the user explicitly re-scopes. See `references/settings-page-and-data-root.md` section Login gate.

## Order entry

- **Order number format (ERP, corrected 2026-07-12):** `MD` + **`yyyymmdd` (4-digit year)** + 4-digit daily sequence, **no slashes**. Example: `MD202607120001` (not `MD2607120001`). User’s `MDyy/mm/dd0001` is structure-only; do not put `/` into `order_no`. 「26改为2026」means the year part of the number.
- **Sale and return share the same daily sequence** for that **business** calendar day.
- **New documents (sale and return):**
  - Date **defaults to today but is editable**.
  - Order number is **readonly** and **follows the chosen business date**.
  - UI: on date change, call `GET /orders/api/next_order_no?date=YYYY-MM-DD` to refresh the preview number.
  - Backend create: **trust a valid posted `order_date`** (invalid/empty → today); **never trust** posted `order_no`—always `next_order_no(conn, order_date)`.
- **Re-edit / 回看→重编辑:** never regenerate the number; keep original DB `order_no` + `order_date`. Both fields readonly; POST loads existing row and ignores form tampering. Only change lines/customer/status/notes.
- **Historical numbers** (`DEMO-…`, old formats, short-year MD trials) stay as-is on re-edit; only **new** creates use current MD rules.
- `next_order_no` scans `order_no LIKE 'MD{yyyymmdd}%'` and max of `^MD{yyyymmdd}\d{4}$`. Prefer server `next_order_no` over client-then-bump.
- Product entry is Excel-like: type product name, unit, quantity, unit price; save unknown products into the dictionary.
- Customer-specific price should be learned automatically during order entry when entered price differs from product regular/default price.
- Product autocomplete should show both prices when available: `客户价：66.00元 / 常规价：67.00元`.
- Unit price field should prefer customer price but offer a normal-price checkbox; unchecking it must restore the customer price.
- Details and tests: `references/md-order-number-and-date-lock.md`.

## Printing

### Paper truth (销售单，2026-07-14 尺子定案)

- **一联（撕开后打一单）= 241×140mm**（尺量宽约 240 ≈ 行业 241 含导孔）。
- **二联纸**：两联未撕总高 ≈ **280mm**；包装/旧文档 241×280/288 是两联总高或标称，**不是** PDF 单页高度。旧默认 `printer_paper_height_mm=280` 是错把两联当一页。
- **打法**：一单一页 PDF + 二联叠打。`@page` / `.page` 高度 = **140**，不是 280。
- **左孔带到正文 ≈ 20mm**（用户实测）— 左边距按此留。
- **左右边距必须等宽**：左孔边实测 20mm ≠ 可以右只留 10mm。左20/右10 会把整块版心右移，标题 `text-align:center` 只在偏右内容区居中，用户会说「偏右」。
- **落地（当前模板，用户「这居中吗？」后定案）**：不要只靠 `padding: 6mm 20mm` 就宣称居中。用 **`.sheet { position:absolute; left:20mm; right:20mm; top:6mm; bottom:6mm; width:201mm }`** 绝对对称版心；表格 `width/max-width:201mm`，列宽合计必须 = 201（约 12/78/18/24/32/37）。footer 右栏约 **38mm**。`letter-spacing:0` 于公司名（字距会视觉右偏）。
- **认居中前必测**：WeasyPrint 出 PDF → pypdfium2 渲 PNG → 墨迹 bbox 算左右边距 mm；目标 L≈R≈20、差 <1mm、右缘不裁。用户贴预览图说「偏右」时先认错再量，勿辩。
- **列宽**：名称规格固定约 **78mm**，不要 `<col>` 无宽度吞掉整行。用户会嫌名称格「太宽」。
- 短计划：`开发短计划/2026-07-14_销售单241x140二联纸版面_执行计划.md`。盒标与尺子冲突：**以一联尺子为准**。细节：`references/sales-slip-paper-241x140.md`。

### 预览对、实打偏 — 修法优先级（用户纠正）

用户明确：**不是先 offset。** 顺序：

1. 尺子量一联 → 定 `@page` mm（现 241×140）
2. 版心设计（固定分区；孔边 20mm；140 高收紧字号/页脚）
3. 驱动自定义纸 = PDF 页尺寸；关「适合页面」
4. 仅 1–3mm 机差才用 `print_offset_*`（默认 0）。设置页可暴露校准，**禁止**大 offset 掩盖页高错误（2026-07-14 曾试 x=+8/y=-10，用户要求恢复 0）

### Layout rules

- Single-order print: company name, 销售清单, metadata, item table with total row inside table, plain-text footer, signer/receiver inside safe margins. Footer `print_*` from config/settings, not hard-coded.
- Footer: `制单人` same row as 订货电话, `收货人` same row as 订货地址, **shared fixed-width right column** (`footer-grid` / `footer-sign`; under 140mm layout ~**38mm**). `____________` for receiver. **Never** `.maker-sign { translateX(...) }`.
- **Footer under table, not page bottom (user 2026-07-14):** `.bottom { margin-top: 2.5mm; }` in document flow. Do **not** `position:absolute; bottom:0` on 140mm pages — that pins phone/address/sign to the paper edge with a huge empty middle. User:「表格下方的内容不要靠着页面的最下面，紧贴着表格下面就可以了」。
- **Browser preview ≠ WeasyPrint PDF ≠ physical print.** Settings `/settings/print-preview` is Chromium HTML; order print is WeasyPrint. Use fixed two-column footer table, not flex + space-between.
- **Preview legibility (user:「链接我看不出来」):** `/settings/print-preview` is **bare print HTML** (no ERP chrome). On a large monitor the 241×140 strip sits at the top and looks almost blank. Prefer: (1) generate a real PDF under `temp_pdf/` and give the **Windows path** to open, or (2) settings page → 预览打印效果 after login, or (3) order PDF. Do not only paste the preview URL as if it were a normal full-page UI. Still require login for preview routes.
- Verify: (1) template asserts, (2) real PDF + **assert page size mm**, (3) physical print with ruler. Do not claim fixed after only HTML preview. Details: `references/print-offset-calibration.md`.
- After layout changes: restart **5001**, re-probe, give clickable links **and** a sample PDF path when the change is print layout; not stale **5000**/`v0.3.x`.
- Rename user-facing PDF actions to “打印” when the business action is printing.
- “保存/打印订单” should save then open the order print PDF in a separate browser tab/window; do not replace the active order-entry page with the PDF preview.
- For Windows desktop builds using PyWebView/WebView2, never submit a POST form with `target="_blank"`. PyWebView may forward only the popup URI and lose/replay the POST context, causing Flask 405 (`GET /create` or `POST /pdf`). Use fetch POST → `200 JSON` → pure GET PDF URL, and keep a visible manual “打开打印页” fallback plus saved-order status/actions. Implementation and verification details: `references/pywebview-save-print.md`.
- **Desktop print re-login (session split):** opening PDF/print via system browser `target=_blank` does **not** share the pywebview session cookie, so a logged-in desktop user may hit `/login` again. Prefer keep navigation inside the shell; if external browser is required, use a short-lived one-time loopback auth ticket—never default `ERP_DISABLE_AUTH` or long-lived password-in-URL. Cover orders PDF, summary PDF, and settings print-preview entries.
- A `302`→`303` redirect change and successful Flask test-client/curl checks prove only the server path, not the actual desktop popup path. Never report this desktop bug fixed until the PyWebView click itself is exercised.
- When this desktop-only 405 is reported, stop changing code and investigate first: confirm the running EXE/version, compare direct HTTP behavior, capture the exact method+URL emitted by the WebView click, and inspect the packaged template plus installed PyWebView popup handler. Historical investigation notes: `references/pywebview-save-print-405.md`.

## Accounts receivable summaries

- Accounts page filters by customer and optional start/end date; filters can be used independently when supported by the page.
- Provide a print summary for filtered orders.
- Summary table expands order items in chronological order: date → order id → item id.
- Do not merge same products across orders; keep each order item line as-is.
- In printable summaries, merge repeated date cells and repeated order-number cells with table rowspans so each date/order number appears once per group while item lines remain separate.
- If a single order has multiple item rows, merge the per-order total cell too; show the order total once per order instead of repeating the same total on every product line.
- When summaries include returns, print return rows in red, keep sales rows unchanged, and end with a clear formula: `销售总金额 - 退货总金额 = 总金额`. Show return total as a positive deduction value even if return order totals are stored negative.
- When no explicit start/end filter is supplied, show the actual earliest/latest order dates present in the printed table instead of placeholders like “最早/最新”.
- End summary with customer name, date range, formula when applicable, and total amount.
- **Summary PDF order-no overflow (user-reported):** never put long order numbers in a narrow column with `white-space: nowrap`. Prefer order-no col ≥ ~42mm, `word-break: break-all` / `white-space: normal`, slightly smaller monospace font; keep date nowrap if needed. After layout CSS changes, regenerate a real multi-item PDF and check the order-no cell does not spill into 名称规格—HTML string asserts alone are not enough if WeasyPrint metrics differ.
- Document-management export and month-to-month filters: `references/document-management-summary-export.md` (rename 单据查询→单据管理; 导出汇总表 new-tab PDF; keep `/accounts/`; multi-customer sections; sales+returns ignore order_type filter; 按月=start/end year-month, cross-year OK; summary PDF column rules above).

## Return orders

- Add return orders as a sibling flow to sales orders, not as a visually separate mini-form: reuse the same Excel-like order-entry template and vary labels/actions by `order_type` (`sale` / `return`).
- Keep sidebar opening shortcuts explicit under “开单”: `销售单` → `/orders/new`, `退货单` → `/orders/return/new`, so operators can jump directly to either sub-page.
- Persist return orders with an explicit `order_type='return'` field instead of inferring from notes/status/order number.
- For receivables, return orders should reduce the customer's balance by storing the order total as negative. Keep item-level `subtotal_cents` positive if the schema has `CHECK(subtotal_cents >= 0)`; apply the sign only to the order total / ledger calculation.
- Return orders need the same save/print workflow as sales orders when printing is requested: show `保存/打印退货单`, submit `save_action=save_print`, open PDF/print in a separate tab, then return the active entry page to `/orders/return/new` instead of redirecting the operator to the order list.
- In order list and account customer-order tables, mark return-order rows red (Bootstrap `table-danger` is acceptable); leave sales rows unchanged.
- Add a migration helper for existing SQLite databases when adding `order_type`, e.g. `ALTER TABLE orders ADD COLUMN order_type TEXT NOT NULL DEFAULT 'sale' CHECK(order_type IN ('sale','return'))`, so live data remains readable.

## List/filter and lookup UX

- On filtered list pages such as orders and accounts, add a nearby reset link/button labeled `清除筛选条件` that returns to the unfiltered route.
- Customer purchase analytics must use only active sales: `order_type='sale'`, `status IN ('saved','printed')`, and `deleted_at IS NULL`. Never infer validity from amount sign alone.
- After 筛选, show analytics automatically when scope allows—do not require a hand-built exact-name deep link. Scopes: unique customer (exact or unique fuzzy) → that customer; multiple fuzzy matches → list + notice, no chart; empty customer → storewide amount heatmap + quantity top5/10. Details: `references/customer-analytics-and-master-data-import.md`.
- Order-query time filters are three-way: `按年` / `按月` / `按日到日` (`date_mode=year|month|range`). Switching modes shows only the matching controls; list + heatmap both use the resolved bounds. **按月 is month-to-month** (`start_year`/`start_month`～`end_year`/`end_month`, cross-year OK)—not single-month only. See `references/document-management-summary-export.md`.
- Heatmap layout is month-block calendars with gaps (not GitHub dense contribution grid). Wide screens ~6 months per row; narrow screens reflow the **left heatmap only**; keep the right ranking pane from wrapping into multi-row chart shards.
- Wide-monitor ranking blank space: never give the right ranking column a large free `fr` share while canvas stays ~300px default. Cap ranking pane (`minmax(300px, min(420px, 38%))`), set `canvas { width:100% }`, size buffer from pane `clientWidth`, redraw on `resize`/layout. User screenshots of empty space beside a few bars mean layout is wrong—fix it, do not blame zoom.
- “Most purchased” product ranking means numeric quantity descending; use amount and name only as tie-breakers. Because quantity may be stored as text, parse defensively and treat invalid values as zero. Storewide ranking uses the same quantity rule.
- Charts supplement rather than replace auditable values: show a visible ranking table with rank, product name, cumulative quantity, and cumulative amount.
- Validate customer/date/type query parameters before aggregation. Invalid dates must safely fall back to actual transaction bounds or another explicit default, never produce a 500; guard impractically large generated day ranges.
- Treat list filters as composable state. For order queries, customer, date mode/bounds, and order type (`全部` / `只看销售单` / `只看退货单`) must work together; changing one filter should preserve the others.
- Drill-down links from charts, calendars, or summaries should reuse the canonical order-query route with prefilled query parameters. Do not build a second order-detail list merely for analytics.
- Product and customer management pages should support quick lookup by similar characters: a GET `q` field, datalist/autocomplete suggestions, and filtered rows. Add a `清除信息` reset link that returns to the unfiltered management page. Order-query customer box should also suggest live dictionary names while typing.
- Heatmap hover product list: show only the first few names, then `另有X种` when more remain; do not dump every product into `title`.
- Import failure pages (bad suffix/header/empty/oversize) must still render the current product/customer list and import controls with a Chinese error alert. Never return an empty list shell that looks like data was wiped.
- Prefer direct-render `import_result` after POST; remove dormant GET query-string result parsers once unused so large error payloads cannot reappear in URLs.
- Dirty master-data files (客户列表/商品列表 from old systems): do **not** jump to import or silent rewrite. Workflow: open files with openpyxl → quantify structure/header/dupes/phones/fullwidth/connectors → report overview + staged cleaning plan → get user decisions on merge/price/depth → write **new** cleaned xlsx + change log under e.g. `dist/import/cleaned_v1/` (never overwrite source). Template mismatch is a hard gate for **ERP import**: customer `客户名称`; product `商品名称`+`价格` (a single `名称` column fails import). Light name-only product clean without price is OK when user asks first-pass cleanup only. **Do not** promise unit prices from `销售单列表汇总*.xls`—that export is order-level totals only; need line-item detail or a price list. Full checklist: `references/master-data-cleaning-before-import.md`; import API: `references/customer-analytics-and-master-data-import.md`.

## Multi-issue parallel vibe coding (项目)

When the user wants **multiple Vibe Coding / external agents**, each on one GitHub issue:

1. Open issues first on `Astyyym/simple-erp` with clear acceptance criteria (enhancement/bug labels).
2. Produce **one full copy-paste prompt per issue** + a shared execution-boundary table. Do not give only outlines.
3. Branches from latest `main` only: `feature/issue-N-…` / `fix/issue-N-…`. Agents must not base on each other.
4. Default delivery for that round: branch + code + tests + commit (`Fixes #N`). **No** VERSION bump, EXE rebuild, or GitHub Release unless the user explicitly starts the release train.
5. File ownership: put allow-lists in each prompt. Hotspots `orders.py` / `orders/list.html` / `base.html` must not be dual-owned without serializing.
6. Prefer `git worktree` for true parallel; single checkout → serial agents.
7. Suggested merge order: desktop/print auth fixes → Excel export features → pure CSS (glass) last.
8. Prompt class details: skill `vibe-coding-spec` → `references/multi-issue-agent-prompts.md`.

### Known issue cuts (do not blur)

- **Glass UI (Issue #1 pattern):** CSS-only; details in `references/erp-ui-redesign.md` §毛玻璃. Tokens in `base.html` (`--erp-glass-*`). Surfaces: sidebar / topbar / card / table shell / filter `form.row` / dashboard quick-cards / settings-card / order-meta. Keep **inputs solid**. Do **not** touch `orders/print_template.html` or WeasyPrint. Primary CTA solid accent. `@supports not (backdrop-filter)` → solid fallback. Enterprise, not purple AI-slop. **Strength:** first ship restrained (blur 12 / alpha ~0.78); if user says「再强一点」, dial tokens only—blur **16** max, light alpha ~0.58 / dark ~0.48, saturate 1.2–1.35, stronger mesh so blur is visible.
- **Desktop print re-login:** pywebview session ≠ system browser `target=_blank` cookies. Prefer in-shell open; else one-time loopback ticket. Never ship global auth off as the fix. Touch: `auth*`, `desktop_app.py`, print open links in orders/accounts/settings templates.
- **Excel export vs PDF summary:** export = real `.xlsx` for 客户/商品/销售单/退货单 (openpyxl already in requirements). Not another 货款汇总 PDF. Reuse import response style; new `utils/exporting.py`; list-page buttons; login-gated download routes; money via cents→yuan helpers. Shipped pattern (Issue #3): one-row-per-order-item; sales/returns endpoints pin `order_type` and ignore list dropdown; follow customer+date filters; soft-deleted excluded. Full field lists, URLs, test design: `references/excel-data-export.md`. PDF summary stays separate: `references/document-management-summary-export.md`.

### Per-issue agent delivery (when prompt says branch+commit only)

- Stay inside the assigned **git worktree**; never edit sibling worktrees or main checkout.
- Branch already bound: do not re-checkout / do not base on other feature branches.
- Commit with `Fixes #N` or `Refs #N`; **no** merge to main, **no** VERSION/CHANGELOG release train, **no** EXE/ZIP.
- Final reply must list: branch, files, what changed, how to preview (5001), test results, known limits.
- Worktree may lack a virtual environment. Prefer the Windows environment from the authoritative checkout, e.g. `本地 Windows 仓库根目录\.venv-win\Scripts\python.exe`; use `WSL 中映射的同一仓库/.venv/bin/python` only for optional WSL cross-tests.

### After agents finish: land PRs, then clean worktrees

User-facing sequence that matches 项目 multi-issue rounds:

1. Push all feature/fix branches; open **one PR per issue** with `Fixes #N`.
2. Merge **serially**: desktop/print auth → Excel export → glass CSS. Pull `main` after each merge; rebase remaining PR heads if they touch the same templates (`base.html`, `orders/list.html`, `orders/new.html`, `settings/index.html`).
3. For “开 5001 我检查刚合的那条”：only start the optional WSL preview from the authoritative `简单ERP` checkout after confirming it contains the merge. Kill any stale worktree preview first.
4. Delete worktrees **only after** the related PRs are merged (or abandoned) and the user is done accepting that train: `git worktree remove …` then `git branch -d …`. Open PR heads still need their worktree until merged or force-pushed from elsewhere.
5. Still no auto VERSION/EXE/Release in this close-out unless the user starts the release train.

## Short plans and unfinished WIP

- For multi-feature ERP work after a delay/rework, keep executable short plans under the project folder `开发短计划/` (`YYYY-MM-DD_主题_执行计划.md` + index README). Long-lived docs live under `docs/`: requirements in `docs/需求/`, retrospectives in `docs/复盘/`, ops notes + `docs/README.md` index. Short plans stay execution-only (not under `docs/`) and update status in-file. After moving docs, fix relative links in short plans.
- When the user adds substantial new analytics/filter rules mid-stream, **write/update the short-plan Markdown first**, then implement—especially if they explicitly ask for the plan document before coding. When work is split across external vibe agents by issue, short plans are optional per agent; the **issue body + full agent prompt** is the source of truth for that branch.
- **Settings / data-root track (2026-07-12):** prioritize `开发短计划/2026-07-12_设置页_需求与执行计划.md` over the older data-separation short plan; obsolete clauses = first-run force picker and “no settings migrate this version”. Implementation + pitfalls: `references/settings-page-and-data-root.md`.
- Before writing on top of dirty worktrees: produce a keep/patch/rewrite table (`需求 → 文件 → 处置 → 证据`). Do not stack a second implementation on half-finished code without that audit.
- Preferred close-out order when both analytics and import are unfinished: finish order-type filter → import page markers (easy Windows probe) → purchase dashboard → full suite + live DB hash → Windows acceptance gate. Release/git/EXE stay frozen until the user confirms.
- When merging 账款打印 into 单据管理: add export only; leave accounts routes/UI; multi-customer PDF via `sections[]`; export URL drops `order_type` intentionally (`references/document-management-summary-export.md`).
- `orders.py` / `orders/list.html` are conflict hotspots (type filter + date modes + heatmap + drag-select + ranking + summary export). Edit them serially under the main agent; do not let parallel subagents share those files.
- After rule upgrades, regression-test: unique fuzzy customer, multi-match notice, empty-customer storewide, `date_mode=year|month|range` bounds, and month-grid markers (`purchase-month-grid` / `month-calendar`).

## ERP UI redesign

- For visual-only redesigns, preserve routes, field names, business logic, autocomplete, pricing behavior, table stability, and user-visible strings covered by regression tests.
- Prefer an original enterprise UI derived from Carbon/Fiori/Odoo/ERPNext/Airtable principles: restrained palette, business-first navigation, dense readable tables, one primary accent, and semantic status colors. Do not clone a branded product screen.
- Build global design tokens and application-shell styles in `base.html` first, then add page-local styling only where interaction demands it, such as the Excel-like entry grid and suggestion overlays.
- Avoid fake dashboard metrics, decorative data, and Emoji navigation. Expose real high-frequency business actions instead.
- Sidebar hierarchy must be visually unambiguous. Group headings such as “业务中心” and “基础资料” must not be smaller or weaker than their child items; give them a filled label box, visible border or accent stripe, adequate contrast, and spacing before child links.
- For “一键清空” on sales/return entry, place it before “新增一行”, confirm before clearing, remove customer/product/notes and price-option state, collapse extra item rows back to the default one row, recalculate totals, and retain system-generated date/order-number/status fields.
- Treat ERP-owned suggestions and browser-owned autofill as separate systems. Keep customer/product API suggestions, but suppress browser history on transactional fields such as customer, product, unit, quantity, price, order number, and notes. Use form-level `autocomplete="off"` plus field-level autofill-resistant attributes when Chromium/WebView ignores plain `off`; password-manager ignore attributes may also be needed.
- Autofill suppression varies across Chromium/WebView versions. Regression-test the rendered attributes and ERP suggestion JavaScript, then manually focus the fields in the actual desktop WebView before claiming old browser suggestions are gone.
- Validate automated behavior and rendered desktop UX: clipping, horizontal overflow, stable action columns, row height, open autocomplete/dropdown overlays, JavaScript console errors, and wide-monitor empty regions beside canvas charts (ranking pane must fill its column).
- Full-suite tests must use an automatically isolated temporary database fixture (for example, monkeypatch the database-path function per test), not merely rely on an operator to move `data/erp.db*` aside. Verify the live database checksum is unchanged before/after the suite. If any reviewer or subagent ran tests before isolation existed, inspect for UUID/test-pattern records and restore from the last known pre-run backup before reporting completion.
- Before the user approves the visual direction, keep UI changes local: do not automatically commit, push, bump version, or rebuild the Windows package. **Exception:** full per-issue agent prompt that already requires branch + code + tests + commit (`Fixes #N`) → commit on that feature branch only; still no main merge / VERSION / EXE / Release.
- **Hardcoded page whites:** dashboard/order pages often use `background:#fff`. After theme tokens change (glass/dark), replace with `var(--erp-glass-bg)` / `var(--erp-surface)` so dark/system stay readable.
- **Pinned topbar + keep glass (2026-07-14):** do **not** “fix bleed” by making the topbar solid and killing glass — user rejects that. Correct shell: `.content` = column flex / `overflow:hidden`; `.page-topbar` flex-none **outside** the scrollport (glass tokens stay); `.page-body` flex-1 / `overflow:auto` holds `{% block content %}`. Only pin the status topbar, not page titles/quick-cards. Recipe: `references/erp-ui-redesign.md` §顶栏钉住 + 毛玻璃.
- **Sidebar exact HEX:** when user specifies a sidebar brand color (e.g. `#012121`), set solid `--erp-sidebar` / `--erp-sidebar-glass` in light+dark+system and disable sidebar `backdrop-filter` so the painted color matches the hex.
- Detailed workflow + glass recipe: `references/erp-ui-redesign.md`.

## Page performance

- When a list page displays a calculated value for every customer, do not call a single-customer helper that opens a database connection and runs several queries inside a loop. This is an N+1 query pattern and becomes visibly slow as customer count grows.
- Calculate all balances in one grouped SQL query: aggregate active orders, active payments, and active adjustments by customer, join those aggregates to customers, and preserve the canonical balance formula `opening + orders + adjustments - payments`.
- Measure the affected route with repeated Flask test-client requests before and after optimization, comparing it with ordinary list pages rather than relying on subjective impressions.
- Add a regression test that compares at least one bulk-query balance rendered by the page with the canonical single-customer balance helper.

## Verification

- Add regression tests for reported UI/print behavior, including rendered HTML/PDF-template semantics where possible.
- If tests use the project’s persistent SQLite database and `init_db()` only applies schema instead of clearing data, make new fixture names/order numbers unique (for example with a short UUID suffix) rather than relying on static customer names.
- Prefer an autouse pytest fixture that monkeypatches the production database-path accessor to a fresh `tmp_path` database for every test. Verify that the fixture intercepts the accessor production code actually uses.
- Audit isolation beyond the primary DB accessor. Monkeypatching `erp.db.db_path` does not affect modules that used `from erp.db import db_path`; cached references such as backup utilities can still read/copy the live DB. Prefer runtime module lookup, dependency injection, or patch every cached reference. Isolate backup/output/config directories too, and assert pytest creates no artifacts under the real `backups/` directory.
- When running the full suite against this local ERP, avoid polluting or depending on live business data: temporarily move aside `data/erp.db*`, run tests against a clean database, then restore the original files.
- For independent integration reviews, record targeted-test and full-suite results, hash the live DB before and after, and inspect ignored runtime directories for test-created files. An unchanged DB hash does not prove full isolation when tests copied live data or wrote backups elsewhere.
- Requirements expressed as explicit analytics predicates (for example active sales only) should be enforced in SQL, not by fetching broader rows and filtering in Python. This improves auditability, future-type safety, and efficiency.
- Bulk imports must cap retained/rendered row errors (for example first 50–100), track total error count separately, and offer a downloadable report when appropriate. A file-size cap alone does not prevent an unbounded HTML response.
- Remove obsolete query-string import-result parsing when results are rendered directly; do not leave a dormant path that encourages large error payloads in URLs.
- Include `git diff --check` and source permission inspection in integration review; ordinary Python source should normally be mode `0644`, not executable or world-writable.
- Do not equate “subagent completion notice not received” with “subagent is still running.” Check process/task state and file modification times; if files are stable and tests exist, take over integration instead of leaving the user waiting.
- Use a strict preview gate before saying “可以测试了”: integrated diff reviewed, feature tests and full suite pass, `compileall` and `git diff --check` pass, the exact user-facing process has loaded the new Python code, live routes/downloads are probed, and the actual browser page is visually checked.
- **UI/CSS marker probes (glass, tokens, shell classes):** never trust bare `curl` status codes. Login gate returns **200 + login HTML**. Start 5001 with `ERP_DISABLE_AUTH=1` (or session cookie after POST `/login`) and assert title/shell markers before counting CSS tokens. Details: `references/agent-managed-dev-preview.md` pitfall “Auth false-negative”.
- If the user’s screenshot contradicts your browser/tool output, treat the screenshot as evidence of the instance they actually see. Acknowledge the missing control, investigate process/route/version mismatch, and do not blame refresh/navigation without proof.
- Use explicit status language: “正在开发，暂不可验收”; “阶段性代码已写入，但尚未通过交付门”; or “已通过交付门，可以测试”. Never make the operator repeatedly restart or search for controls in a half-integrated worktree.
- Full workflow and diagnostics: `references/user-visible-instance-verification.md`; focused WSL/Windows localhost collision recipe: `references/wsl-windows-localhost-instance-mismatch.md`.

## Development environment: Windows primary + optional WSL cross-test

- The only authoritative checkout is `本地 Windows 仓库根目录` on Windows.
- Windows-native Hermes performs normal editing, tests, PyWebView/WebView2 work, PyInstaller packaging, PDF/printing checks, and EXE acceptance directly in that checkout.
- WSL may access the same files at `WSL 中映射的同一仓库` for optional Linux cross-tests. It is not the primary environment and must not hold a second durable repository.
- Keep ports unambiguous: Windows source/EXE uses `127.0.0.1:5000`; optional WSL preview uses `5001` and must never open or kill the Windows instance.
- Migrating Hermes/MCP to Windows does not require ERP business-code changes unless the ERP directly depends on those components.
- Current launchers and operational docs use the new project name. Historical requirements/retrospectives may retain old business names when meaningful.
- Hermes Desktop workspace registration is separate from repository code; the active project is `简单ERP` bound to the Windows path.
- CRLF BAT pitfall: `git diff --check` can report every changed `.bat` line as trailing whitespace when CR is not accepted at EOL. Verify the bytes are normal CRLF, then use repo-local `git config core.whitespace cr-at-eol` for validation. Do not add or renormalize `.gitattributes` casually—`git add --renormalize` can create unrelated whole-file BAT diffs. Restore any unrelated line-ending-only changes before committing.
- Before pushing this maintenance class, fetch and require `HEAD...origin/main = 0 0`, stage an explicit allow-list, run staged `git diff --cached --check`, push, then verify local HEAD equals `origin/main` and read back remote README/launcher markers. A source-only maintenance push does not imply a version bump, EXE rebuild, ZIP, or GitHub Release.

## Deployment / parent-friendly startup

- For parents/non-technical shop users, the final deliverable must be a Windows-native desktop app experience: install/copy a folder, place a desktop shortcut to an `.exe`, double-click, and use it. Do **not** present WSL launchers or command-line activation as the final answer.
- Treat WSL as a development environment only. A `.bat` that calls `wsl.exe` may be useful for the developer, but it is not acceptable for the parents’ computer because they may not have WSL.
- Preferred production packaging for this ERP class: a PyInstaller one-folder build with a small desktop wrapper (`desktop_app.py`) that starts Waitress on `127.0.0.1:5000`, opens PyWebView/WebView2 or browser fallback.
- **Settings page + data root + login (shipped v0.5.0, 2026-07-12):** full rules in `references/settings-page-and-data-root.md` + short plan `开发短计划/2026-07-12_设置页_需求与执行计划.md` (section 九 documents mid-stream login + pre-package). Essentials: no first-run force folder picker; EXE default `Documents\<AppName>数据`, source default project root; path memory only in `%LOCALAPPDATA%\<AppName>\data_location.json`; settings change root with **desktop browse folder + one-click migrate** (refuse non-empty target `erp.db`; keep old folder); company name one field → `shop_name`+`app_name` + topbar/window title/all PDF headers; UI scale 100/125/150 + theme light/dark/system UI-only; print footer fixed-slot text + sample preview; **dirty leave guard**; single-account login. Desktop: `desktop_app.DesktopApi` + `ERP_DESKTOP` + PyWebView `js_api` for folder dialog and title sync. Folder picker needs EXE smoke (not browser 5001). **Settings before** remaining separation close-out (old green-folder probe still open). Writable `config.json` for app settings; `project_path` business dirs under `data_root`.
- **Green-folder upgrade handoff:** not an MSI. Until every install uses separated data_root, still: backup `data/`; close EXE; overwrite program only; never clobber live shop DB via folder copy. Details: `references/green-folder-upgrade-and-data.md`.
- Build with Windows Python in a separate `.venv-win`; never use the WSL venv to produce a Windows exe.
- When adding packaging support, update runtime path handling for frozen apps: bundled templates/config may live under `sys._MEIPASS`, while writable runtime files should live beside `sys.executable`.
- Daily startup must not run `pip install`; dependency installation belongs in setup/build scripts only.
- Windows WeasyPrint packaging pitfall: before importing app modules that import WeasyPrint, add GTK/Pango DLLs to the DLL search path (`os.add_dll_directory`, `PATH`, and/or `WEASYPRINT_DLL_DIRECTORIES`). If PyInstaller EXE starts but `/health` never comes up, check for `gobject-2.0-0`/Pango load errors; install GTK3 runtime on the build machine and bundle its DLLs in the one-folder distribution.
- **Rebuild dist lock:** if PyInstaller cannot replace `dist/简单ERP`, a previous `简单ERP.exe` is usually still running from that folder (often on port 5000). Stop it first; smoke from an ASCII temporary copy rather than keeping the real `dist/` process open.
- Smoke-test the built artifact before claiming readiness: launch `dist/简单ERP/简单ERP.exe` (or its ASCII temp copy), then require `http://127.0.0.1:5000/health` to return `ok` and the new VERSION.
- Desktop workflow bugs require desktop workflow verification. For popup/navigation/form issues, `/health`, curl, Flask tests, and direct PDF generation are insufficient; automate or manually exercise the real PyWebView/WebView2 click and capture its network request before claiming success.
- **WSL source preview vs stale Windows localhost service:** Before saying new source changes are visible at `http://127.0.0.1:<port>`, verify the response from the same client side the user uses—normally Windows PowerShell/browser—not only from curl inside WSL. Windows localhost forwarding or an already-running packaged Waitress/EXE can own the same port and serve an older build while WSL-local requests reach the new Flask process.
  1. Inspect listeners from both WSL and Windows; identify PID, executable and response server (`Werkzeug` vs `waitress`).
  2. Probe a unique marker introduced by the change, such as `下载Excel模板`, not merely `/health`, company title, or an unchanged version string.
  3. If ownership is ambiguous, stop the stale service or bind the WSL preview to `0.0.0.0` on a different port; verify from Windows using the current WSL IP.
  4. Give the user only the exact URL that passed this Windows-side marker probe.
  5. Never blame cache, refresh, wrong page, or user navigation until the HTML served to their actual browser side has been proven.
- Default port split for this project: production desktop/EXE `127.0.0.1:5000`; WSL source preview `0.0.0.0:5001` and tell the user the WSL IP URL only after Windows-side markers pass.
- **Agent-managed preview (user preference):** after implement/gate, keep 5001 up yourself; give clickable markdown links; do not instruct the user to manually activate venv and restart `app/app.py` for routine acceptance. If the link dies (WSL reboot), restart and re-send the URL. Default `config.json` (`127.0.0.1:5000`) is **not** the preview contract. See `references/agent-managed-dev-preview.md`.
- Windows PowerShell probes for Chinese UI strings are fragile when the `.ps1` encoding is mangled. Prefer ASCII-stable markers in the probe (`/products/import/template`, `id="purchaseHeatmap"`, `id="customerPurchaseData"`, `name="order_type"`, Server header) and build Chinese query values with `[char]0x....` or URL-encoded forms rather than embedding raw Chinese literals in the script file.
- Seed a clearly named demo customer/orders for acceptance when live data is sparse; note that the demo is temporary and offer cleanup after the user finishes testing.
- Code written by a subagent is not automatically “available” or “ready to test.” Keep status as “正在开发，暂不可验收” until integration tests pass and the user-visible instance is verified from the user's side.

## GitHub backup and versioned releases

- Keep source in a private GitHub repo for remote backup, but never commit live business data or build products. `.gitignore` should exclude `data/`, `logs/`, `temp_pdf/`, `backups/`, `imports/`, `dist/`, `build/`, `.venv*`, `*.db`, `*.db-wal`, and `*.db-shm`.
- Add a simple `VERSION` file and surface it in the app UI plus `/health`, so the operator can tell which build is installed.
- Maintain `CHANGELOG.md`; bump patch versions for bug fixes (`v0.1.1`), minor versions for new user-visible features (`v0.2.0`), and reserve `v1.0.0` for the first stable production release.
- Release workflow: edit source → tests → bump `VERSION`/`CHANGELOG.md` → commit/push → rebuild EXE → smoke-test EXE → copy the new program files to the parents’ PC while preserving `data/erp.db`.
- When the user asks to update README or publish a release after feature work, treat docs/version/package as one release task:
  1. Update `README.md` with current features, usage, architecture, and data/upgrade notes.
  2. Bump `VERSION` and mirror it in README/current UI health output.
  3. Add a `CHANGELOG.md` section summarizing user-visible features and fixes.
  4. Run `PYTHONPATH=app pytest tests/ -q` before committing.
  5. Commit/push docs/version changes to GitHub and verify `HEAD == origin/main`.
  6. Rebuild `dist/简单ERP/简单ERP.exe` and overwrite the current local ZIP(s).
  7. Smoke-test the built EXE from Windows: stop any source/dev process on port 5000, launch the EXE, query `/health` from Windows, require `status=ok` plus the new version, and generate/open one print PDF.
  8. Preserve the configured external business-data root. If smoke tests create demo data or PDFs, remove only those test artifacts before creating the final ZIP.
  9. Recreate the ZIP only after smoke cleanup. If `Compress-Archive` encounters transient locks, ensure no packaged process uses `dist/` and use Python `zipfile` as deterministic fallback; verify final EXE/ZIP sizes and checksums.
- Packaging scripts may place `VERSION` under `dist/简单ERP/_internal/VERSION`; verify via `/health` rather than assuming a top-level file.
- **Chinese-path Windows smoke:** build and run from the authoritative Windows path. If a tool mishandles Chinese paths, copy the one-folder build to an ASCII temporary path for smoke testing, but package the real `dist/简单ERP` tree afterward. Use ASCII release asset names such as `simple-erp-windows-vX.Y.Z.zip`.
- When the user says package + push + overwrite local EXE/ZIP: treat as one release task (VERSION/CHANGELOG/README → pytest → commit/push → bat rebuild → Windows smoke → overwrite zips). Do not commit `dist/`.
- **GitHub Releases ≠ git push.** Committing `VERSION`/`main` does **not** publish installers. When the user asks whether Releases has a package, check with `gh release list` / `gh api .../releases` and answer from evidence. If they then say push the package: `gh release create vX.Y.Z <ascii-zips> --title ... --notes ...` (or upload into an existing tag).
- **Release asset filenames must be ASCII.** Use `simple-erp-windows-vX.Y.Z.zip`; keep any Chinese local display name only under `dist/`. After creation, verify asset names and sizes with `gh release view`.
- Local `dist/` hygiene on release: keep current one-folder app + current versioned zips; drop previous major/minor zips (e.g. remove v0.3.0 when shipping v0.4.0). Still never git-commit `dist/`.
- Repo layout after 2026-07-12 tidy: long-lived docs under `docs/需求/`, `docs/复盘/`, `docs/README.md`; execution plans in `开发短计划/`; no root `temp_*` probes. Keep this shape.
- References: `references/windows-native-exe-packaging.md`; `references/versioning-github-backup.md`; `references/windows-chinese-path-exe-smoke.md`; `references/agent-managed-dev-preview.md`; `references/green-folder-upgrade-and-data.md`; `references/settings-page-and-data-root.md`.

## Useful references

- Session-specific implementation notes: `references/erp-session-notes.md`.
- Document management rename + multi-customer summary export + month ranges: `references/document-management-summary-export.md`.
- Excel data export (客户/商品/销售/退货 xlsx, not PDF): `references/excel-data-export.md`.
- Customer analytics scopes, import rules, heatmap layout: `references/customer-analytics-and-master-data-import.md`.
- Dirty Excel master-data overview + staged cleaning before import: `references/master-data-cleaning-before-import.md`.
- MD order numbers (4-digit year) + new-order editable date / re-edit lock: `references/md-order-number-and-date-lock.md`.
- Windows one-folder EXE smoke and versioned ZIP checklist: `references/windows-chinese-path-exe-smoke.md`.
- Agent-owned 5001 clickable preview (+ worktree borrows main `.venv`): `references/agent-managed-dev-preview.md`.
- UI redesign + restrained glass tokens (Issue #1): `references/erp-ui-redesign.md`.
- Green-folder upgrade vs WeChat-like data separation: `references/green-folder-upgrade-and-data.md`.
- Settings page + data_root + login + dirty leave + desktop browse/title + print footer table + release notes: `references/settings-page-and-data-root.md`.
- Sales slip paper 241×140 (二联; not 280 page height): `references/sales-slip-paper-241x140.md`.
- Print offset = last-resort mm tweak only: `references/print-offset-calibration.md`.
