#!/usr/bin/env node
// Measure stacking depth of every table region on a page, to verify a
// "one container layer" / de-stacking requirement by OUTCOME, not by rule text.
//
// Usage: node audit-glass-depth.mjs <url> [cdpPort] [selector]
//   <url>       page URL to audit (e.g. http://127.0.0.1:5057/purchases/return/new)
//   [cdpPort]   remote debugging port of the already-open Chrome (default 9222)
//   [selector]  optional extra selector to also measure (default: the table selectors below)
//
// Exits non-zero when any measured region's glass/blur ancestor depth exceeds 1.
// A green structural test (rule string present) does NOT prove the layering is
// gone — a glass card nested inside another glass card still stacks two layers.
//
// It connects to an existing Chrome over CDP (no bundled browser).

const url = process.argv[2];
const port = process.argv[3] || '9222';
const extraSelector = process.argv[4] || null;
if (!url) {
  console.error('usage: node audit-glass-depth.mjs <url> [cdpPort] [selector]');
  process.exit(2);
}

async function main() {
  const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = list.find((t) => t.type === 'page');
  if (!page) throw new Error('no debuggable page found on port ' + port);

  const WebSocket = (await import('ws')).default;
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const send = (method, params = {}) => new Promise((res) => {
    const mid = ++id;
    pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  ws.on('message', (raw) => {
    const msg = JSON.parse(raw.toString());
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg.result); pending.delete(msg.id); }
  });
  await new Promise((r) => ws.on('open', r));

  await send('Page.enable');
  // Never trust a cached stylesheet: a versioned URL is served from disk cache.
  await send('Network.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Page.navigate', { url });
  await new Promise((r) => setTimeout(r, 1500));

  const expr = `(() => {
    const extra = ${JSON.stringify(extraSelector)};
    const sel = ['table.table:not(.order-table)', '.order-meta', '.source-preview', '.card', '.filter-card'];
    if (extra) sel.push(extra);
    function blurDepth(el){
      let d = 0, node = el;
      while (node && node !== document.body) {
        const s = getComputedStyle(node);
        const bf = s.backdropFilter || s.webkitBackdropFilter || 'none';
        if (bf && bf !== 'none' && !bf.includes('blur(0')) d++;
        node = node.parentElement;
      }
      return d;
    }
    const out = [];
    for (const s of sel) {
      for (const el of document.querySelectorAll(s)) {
        out.push({ selector: s, cls: (el.className || el.tagName).toString().slice(0, 80), depth: blurDepth(el) });
      }
    }
    return JSON.stringify(out);
  })()`;
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
  const rows = JSON.parse(r.result.value);
  let worst = 0;
  for (const row of rows) {
    worst = Math.max(worst, row.depth);
    console.log(`depth=${row.depth}  ${row.selector}  ${row.cls}`);
  }
  console.log(`WORST DEPTH: ${worst}`);
  ws.close();
  process.exit(worst > 1 ? 1 : 0);
}

main().catch((e) => { console.error(e.message); process.exit(2); });
