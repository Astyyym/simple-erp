# Desktop print session (pywebview vs system browser)

Shipped on branch `fix/issue-2-desktop-print-auth` (Issue #2, base v0.5.0). Prefer this over inventing tokens unless external browser is mandatory.

## Symptom (historical, pre no-login)

Desktop EXE already in-app → click 保存/打印 / 列表打印 / 汇总导出 / 设置预览 → system browser opens. With the old login gate that meant a second `/login`; after permanent no-login (2026-07-26) it is still a **shell vs external-browser** split for session-bound PDF UX.

## Root cause

`target="_blank"` / `window.open(..., '_blank')` leaves the pywebview WebView2 process. System browser has a separate cookie jar and does not inherit the Flask session cookie.

This is a **navigation/session split**, not an auth feature. Related but different from the older 405 save-print POST popup bug (`references/pywebview-save-print.md`). Auth product default is now permanent no-login — see `references/permanent-no-login.md`.

## Preferred fix (Scheme A — in-shell)

Keep print/PDF/preview navigation **inside** the desktop shell when `ERP_DESKTOP=1`.

### Server

```python
# app/erp/auth.py
def is_desktop_shell() -> bool:
    return env_truthy("ERP_DESKTOP") or env_truthy("ERP_DESKTOP_SHELL")

# app/erp/__init__.py context_processor
"is_desktop": is_desktop_shell(),
```

`desktop_app.py` already sets `os.environ.setdefault("ERP_DESKTOP", "1")`.

### Templates

- `base.html`: `data-desktop="{{ '1' if is_desktop else '0' }}"`
- Shared helpers:

```javascript
function isDesktopShell() {
  return document.documentElement && document.documentElement.dataset.desktop === '1';
}
function openPrintUrl(url) {
  if (!url) return null;
  if (isDesktopShell()) {
    window.location.assign(url);  // same window → same session
    return null;
  }
  return window.open(url, '_blank', 'noopener');
}
```

- Print anchors (orders new/list/detail, accounts summary, settings print-preview):
  - Desktop: **no** `target="_blank"`
  - Browser: keep `target="_blank" rel="noopener"`
- Save/print JS: `openPrintUrl(result.pdf_url)` instead of hard-coded `window.open`

### Security / product boundary

- `ERP_DESKTOP` only changes **open mode** (in-shell vs new tab), never invent a second auth system.
- Product default (2026-07-26+): permanent no-login; PDF/export routes open without a session. Do not reintroduce “must login for PDF” unless 用户 asks.
- `/health` stays public with `auth: disabled`.
- Never put long-lived password or reusable token in the print URL.

## Fallback (Scheme B — only if external browser is required)

Short-lived one-time signed ticket for loopback only; consume on first hit; expire quickly. Prefer no DB (HMAC with `SECRET_KEY`). Not used for Issue #2 once A works; even less needed after no-login.

## Tests to keep

- `tests/test_desktop_print_auth.py`: PDF/summary/preview open without login (`%PDF` / 200); desktop marker + `openPrintUrl` helpers present; logout does not re-lock access.
- `tests/test_print_workflows.py`: browser keeps `_blank`; desktop print links omit `_blank` for PDF/summary/settings entries.
- `tests/test_auth_login.py` asserts free access / login route redirects home.

## UX tradeoff (accepted)

Desktop print replaces the current shell page with the PDF (user can navigate back). Browser mode still opens a new tab so the ERP page stays.

## Manual acceptance

1. Desktop EXE: save/print, list print, summary export, settings preview — stay in-shell, no credential prompt.
2. Browser: same URLs open without `/login`; `/health` → `auth: disabled`.
3. Do not claim fixed from Flask test-client alone for desktop UX; shell click still matters for final EXE acceptance.
