#!/usr/bin/env node
// Audit two defects the user reports together and that share one root cause family:
//
//   1. SIDEBAR OWNERSHIP — which sidebar entry reads as selected on each page.
//   2. SCROLL ACROSS A FILTER SUBMIT — the pane's scrollTop before/after clicking
//      the page's primary submit. The app shell scrolls an inner pane
//      (.page-body), not the document, so history.scrollRestoration cannot help
//      and a method="get" submit always lands back at 0.
//
// Usage: node audit-nav-and-scroll.mjs <baseUrl> [cdpPort]
//   <baseUrl>  e.g. http://127.0.0.1:5057
//   [cdpPort]  remote debugging port of the already-open Chrome (default 9222)
//
// Prerequisites (a bare "fetch failed" means the first one is missing):
//   - a Chrome already running with remote debugging enabled, e.g.
//       chrome --remote-debugging-port=9222 --user-data-dir=/tmp/cdp-profile about:blank
//   - Node 22+ (its global WebSocket is used; no `npm i ws`).
//
// Exits non-zero when a page highlights a different set of entries than expected,
// or when a page loses its scroll offset on a GET-form submit.
//
// POST forms are skipped on purpose: saving settings (or any other POST) is not
// a filter, it navigates by design, and landing back at the top is correct —
// testing it produces a permanent false failure.
//
// Connects to an existing Chrome over CDP (no bundled browser).

const base = process.argv[2];
const port = process.argv[3] || '9222';
if (!base) {
  console.error('usage: node audit-nav-and-scroll.mjs <baseUrl> [cdpPort]');
  process.exit(2);
}

// Edit to the module's real routes: one row per sidebar entry, plus every
// cross-module page (a screen reached from module A but served under module B —
// e.g. a reconciliation page under /analytics/ that is linked from /accounts/).
// The third field lists the sidebar labels that may legitimately be highlighted
// on that page, as an ARRAY. Do NOT assume "exactly one": a parent entry and its
// sub-item are correctly lit together on an entry page, and a naive one-item
// expectation reports those pages as failures forever.
const PAGES = [
  ['/', '工作台', ['工作台']],
  ['/orders/', '单据管理', ['单据管理']],
  ['/orders/new', '开单', ['开单', '销售单']],
  ['/orders/return/new', '退货单', ['开单', '退货单']],
  ['/accounts/', '账款管理', ['账款管理']],
  ['/analytics/', '数据分析', ['数据分析']],
  ['/products/', '商品管理', ['商品管理']],
  ['/customers/', '客户管理', ['客户管理']],
  ['/recycle/', '回收站', ['回收站']],
  ['/settings/', '系统设置', ['系统设置']],
];

async function main() {
  let list;
  try {
    list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  } catch (e) {
    throw new Error(`cannot reach Chrome on port ${port} (${e.message}). ` +
      'Start Chrome with --remote-debugging-port=' + port + ' first.');
  }
  const page = list.find((t) => t.type === 'page');
  if (!page) throw new Error('no debuggable page found on port ' + port);

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const send = (method, params = {}) => new Promise((res) => {
    const mid = ++id;
    pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data.toString());
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg.result); pending.delete(msg.id); }
  });
  await new Promise((r) => ws.addEventListener('open', r));
  await send('Page.enable');
  await send('Runtime.enable');
  // Never trust a cached stylesheet or page: a versioned URL is served from cache.
  await send('Network.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });

  const evaluate = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text || 'evaluate failed');
    return r.result.value;
  };
  const navigate = async (url) => {
    await send('Page.navigate', { url });
    await new Promise((r) => setTimeout(r, 1200));
  };

  // Highlight detection must NOT key on a single hardcoded muted colour.
  // A sidebar commonly has TWO muted levels (section entries and indented
  // sub-items use different --tokens), so `colour !== oneConstant` flags every
  // sub-item as highlighted and the whole run reports FAIL on a correct page.
  //
  // The active treatment is the one with a painted background. A secondary
  // "current/ancestor" class reuses the ACTIVE text colour while carrying no
  // background — that pairing is exactly the double-highlight to catch.
  const highlightProbe = `(() => {
    const entries = [...document.querySelectorAll('.sidebar a')].map(a => {
      const cs = getComputedStyle(a);
      return {label: a.textContent.trim(), color: cs.color, bg: cs.backgroundColor};
    });
    const painted = entries.filter(e => e.bg !== 'rgba(0, 0, 0, 0)');
    const activeColor = painted.length ? painted[0].color : null;
    const flagged = entries.filter(e =>
      e.bg !== 'rgba(0, 0, 0, 0)' || (activeColor !== null && e.color === activeColor));
    return {
      flagged: flagged.map(e => e.label + '|bg=' + e.bg + '|c=' + e.color),
      paintedCount: painted.length,
      colorsSeen: [...new Set(entries.map(e => e.color))],
    };
  })()`;

  const scrollProbe = `(() => {
    const p = document.querySelector('.page-body');
    if (!p) return null;
    return { sh: p.scrollHeight, ch: p.clientHeight };
  })()`;

  let failures = 0;
  for (const [path, label, expected] of PAGES) {
    await navigate(base + path);

    const nav = await evaluate(highlightProbe);
    const flaggedLabels = nav.flagged.map(f => f.split('|')[0]);
    const bad = JSON.stringify([...flaggedLabels].sort()) !== JSON.stringify([...expected].sort());
    if (bad) failures += 1;
    console.log(`${bad ? 'FAIL' : 'ok  '} nav    ${label.padEnd(10)} ${path.padEnd(28)} -> ${JSON.stringify(nav.flagged)}`);
    if (bad) console.log(`      expected ${JSON.stringify(expected)}; muted colours seen: ${JSON.stringify(nav.colorsSeen)}`);

    // Scroll across the primary submit, only where the pane actually scrolls.
    const box = await evaluate(scrollProbe);
    if (box && box.sh > box.ch) {
      await evaluate(`(() => { const p = document.querySelector('.page-body'); if (p) p.scrollTop = 300; })()`);
      const before = await evaluate(`(() => { const p = document.querySelector('.page-body'); return p ? p.scrollTop : null; })()`);
      const clicked = await evaluate(`(() => {
        // A plain <button> with no type attribute is an implicit submit and
        // matches no [type=submit] selector, so match by behaviour instead:
        // either an explicit submit button or an untyped button.
        const form = [...document.querySelectorAll('form')].find(f =>
          (f.getAttribute('method') || 'get').toLowerCase() === 'get');
        if (!form) return 'none';
        const b = form.querySelector('button:not([type]), button[type=submit]');
        if (!b) return 'none';
        b.click();
        return b.textContent.trim();
      })()`);
      await new Promise((r) => setTimeout(r, 1500));
      const after = await evaluate(`(() => { const p = document.querySelector('.page-body'); return p ? p.scrollTop : null; })()`);
      const lost = clicked !== 'none' && before > 0 && after === 0;
      if (lost) failures += 1;
      console.log(`${lost ? 'FAIL' : 'ok  '} scroll ${label.padEnd(10)} before=${before} submit=${clicked} after=${after}`);
    } else {
      console.log(`--   scroll ${label.padEnd(10)} 页面不可滚动，跳过`);
    }
  }

  ws.close();
  console.log(failures ? `FAILURES: ${failures}` : 'ALL CLEAR');
  process.exit(failures ? 1 : 0);
}

main().catch((e) => { console.error(e.message); process.exit(2); });
