# PyWebView/WebView2 save-and-print workflow

## Symptom

A Flask form submitted from the desktop WebView with `target="_blank"` opens the system browser, which displays `405 Method Not Allowed`.

## Root cause

PyWebView's EdgeChromium `NewWindowRequested` handler marks the popup handled and opens only the requested URI externally. A POST form's method/body/redirect context is not reliably transferred across the WebView → system-browser boundary. Changing Flask `302` to `303` does not fix this because the break can occur before the redirect is followed.

## Evidence pattern

Confirm all layers before editing:

1. Identify the actual running EXE and owning process for port 5000.
2. Verify `/health` reports the expected packaged version.
3. POST directly to the running EXE and inspect status/Location.
4. Confirm the packed template still contains or no longer contains `target="_blank"` logic.
5. Distinguish the two likely 405s: `GET /orders/create` versus `POST /orders/<id>/pdf`.

## Durable implementation

- Never open a new browser window by submitting a POST form with `_blank` inside PyWebView.
- Intercept save/print with JavaScript and `preventDefault()`; preserve the current WebView navigation context rather than relying on authentication state.
- POST with `fetch` and `X-Requested-With: fetch`.
- Return `200 JSON` containing `order_id`, `order_no`, absolute `pdf_url`, `detail_url`, and the correct sale/return `next_url`.
- Open the PDF separately with a pure GET URL.
- Show a persistent success panel with manual actions: open print page, view order, continue order entry.
- Do not immediately clear or redirect the entry page.
- Disable the submit button while saving; distinguish “save failed” from “saved but popup blocked.”
- Keep ordinary non-JavaScript submission as a fallback, using `303` to a GET page.

## Verification

- Add Flask tests for both sale and return JSON responses.
- Assert templates no longer dynamically assign POST forms to `_blank`.
- Run the full suite against a clean temporary database.
- Rebuild the Windows EXE and ZIP.
- Start the real EXE and verify `/health` reports the new version.
- Against the EXE, POST with the fetch header for both sale and return, then GET each returned PDF URL and check for `%PDF`.
- For the final acceptance, manually click save/print inside the real desktop WebView; HTTP probes alone do not exercise `NewWindowRequested`.

## Scrollbar-free in-page PDF display

- Use an application-controlled rendering layer for the real PDF pages when the screen preview needs hand panning; hiding the native reader's outer scrollbar does not control its internal mouse/scroll behavior. Keep the original PDF bytes separately for download, native save and print; never print the ERP page or rasterize the canvas back into the authoritative PDF.
- Vendor the display library and matching worker/resources with their licenses locally, and include the entire Flask static directory in the PyInstaller spec. Verify `.mjs` responses have a JavaScript MIME type; a source preview passing does not prove a frozen build contains its assets.
- Clip the preview viewport, capture the pointer on press, release on up/cancel/lost capture/close, and clamp each translation axis so all paper edges remain reachable. Center an axis when the paper fits; reset to the top on an overflowing vertical axis. Confirm pan/zoom using real mouse events, not only DOM string assertions.
- Render all original pages. Use a clearly labelled real generated multi-page PDF fixture to test multi-page panning if the business generator currently produces one page; do not describe the fixture as a saved multi-page order.
- Report native print dispatch, the native dialog, and actual printer output separately. A hidden original-PDF frame or a mocked `print()` invocation proves only its code path, not the desktop print dialog or physical output.
- Treat a `%PDF` header check as insufficient for validity. Evict cached bytes/object URLs and reset native-print readiness when parsing/rendering fails, then verify explicit retry fetches and renders the recovered server PDF without saving another order. Use a deliberately truncated real PDF fixture with the real renderer, not only a mock throwing an error.
- Reveal the existing panel before an in-flight-load guard, so close/reopen does not drop the reopen click or start duplicate render tasks. Test both reopening and remaining closed until rendering completes; completion text must follow the actual panel state.

## Packaging pitfall

Before rebuilding or recompressing `dist/消防ERP`, terminate every running packaged ERP process. Otherwise PyInstaller or `Compress-Archive` can fail on locked files such as `logs/app.log` or bundled DLLs. Preserve and restore writable `data/`, `logs/`, `temp_pdf/`, `backups/`, and `imports/` around smoke tests.