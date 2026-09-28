# PyWebView/WebView2 save-print 405 investigation

Use when the Windows desktop ERP shows `Method Not Allowed` after clicking a save-and-print button.

## Known failure boundary

A form submitted with `method="post"` and dynamically assigned `target="_blank"` is not equivalent across normal browsers and PyWebView/Edge WebView2.

PyWebView handles WebView2 `NewWindowRequested` by marking the popup handled and opening only `args.get_Uri()` externally (or loading that URI in the existing view). A POST form's body/method/redirect context may therefore be lost or replayed incorrectly at the WebView → external-browser boundary.

Typical intended flow:

```text
POST /orders/create
→ 303 Location: /orders/{id}/pdf
→ GET /orders/{id}/pdf
```

Possible broken requests that both produce Flask 405:

```text
GET  /orders/create
POST /orders/{id}/pdf
```

Changing `302` to `303` alone does not prove the desktop popup flow is fixed, because the failure can occur before or while PyWebView creates the new window.

## Investigation sequence — do not modify first

1. Confirm the exact running EXE path and `/health` version; do not assume the latest `dist` build is the process listening on port 5000.
2. Submit the same form directly with curl and record status plus `Location`. This separates Flask behavior from WebView behavior.
3. Confirm the PDF URL works with a plain GET and produces `%PDF`.
4. Inspect the packaged template under `dist/.../_internal/erp/templates/...`, not only source, to confirm the EXE contains the same JavaScript.
5. Inspect PyWebView's Windows `NewWindowRequested` implementation for the installed version.
6. Capture the exact failing method and URL from the desktop click. Add temporary request/access logging or use WebView2 DevTools/network capture. A generic 405 page alone cannot distinguish `GET /create` from `POST /pdf`.
7. Only after the exact failing request is known should a fix be designed.

## Required feedback loop

A valid regression check must exercise the actual PyWebView/WebView2 desktop path. Flask test-client or curl checks are necessary but insufficient: they prove server redirects, not `_blank` popup handling.

## Design direction after confirmation

Avoid relying on a cross-window POST submission. Prefer a two-stage flow where the desktop page saves via same-window POST/fetch, receives a stable GET PDF URL, then explicitly opens that GET URL. Choose the concrete implementation only after network evidence confirms the failing request.
