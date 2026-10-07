# Vendored PDF.js display assets

- Package: Mozilla `pdfjs-dist`, pinned to `6.3.289` (npm registry package).
- Upstream: https://github.com/mozilla/pdf.js
- Main license: Apache-2.0, retained in `LICENSE`.
- Sources: `build/pdf.min.mjs`, matching `build/pdf.worker.min.mjs`, `cmaps/`, `standard_fonts/`, `wasm/`. Each resource directory retains its upstream license files.
- Original tarball SHA-256: `06f25e887adc6489f04c9fcb14198c77e4e5623a59a0bba5c4cea5838a4f1241`.
- Reproduction: run `npm pack pdfjs-dist@6.3.289` in a scratch folder; copy only the listed members and their licenses from `package/`. Do not copy `node_modules` or execute package install scripts.
- Runtime assets are served locally by Flask and included by `简单ERP.spec` under `erp/static`; no CDN, Internet connection, Node runtime, or npm installation is required on the user's machine.
- Screen preview renders the generated PDF's pages to canvases. Download/native save/print continue to use the unchanged original PDF bytes, never a screenshot or HTML receipt.
