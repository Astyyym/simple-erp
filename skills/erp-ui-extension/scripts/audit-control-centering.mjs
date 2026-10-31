// Audit vertical label centring for every control on a page, over CDP.
//
// Usage:  node audit-control-centering.mjs <url> [cdpPort] [selector]
//   e.g.  node audit-control-centering.mjs http://127.0.0.1:5001/orders/ 9333 'a.btn, button.btn, label.btn'
//
// Start the browser first with a remote-debugging port on a throwaway profile:
//   msedge.exe --remote-debugging-port=9333 --user-data-dir=<scratch> --no-first-run about:blank
//
// Why a line-box marker instead of the text range: selecting the contents with a
// Range measures glyph ink, which for CJK is legitimately asymmetric by well under a
// pixel. A hidden inline marker inheriting font/line-height measures the LINE BOX, so
// a non-zero offset means a real centring defect rather than a font artifact.

const url = process.argv[2];
const port = process.argv[3] || '9333';
const selector = process.argv[4] || 'a.btn, button.btn, label.btn, .btn';
const DEBUG = `http://127.0.0.1:${port}`;
if (!url) { console.error('usage: node audit-control-centering.mjs <url> [cdpPort] [selector]'); process.exit(2); }

const MEASURE = (sel) => `(() => {
  const out = [];
  document.querySelectorAll(${JSON.stringify(sel)}).forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.height < 4 || r.width < 4) return;
    const cs = getComputedStyle(el);
    const parent = el.parentElement ? getComputedStyle(el.parentElement).display : 'none';
    // line box via a hidden marker that inherits the control's own font metrics
    const mk = document.createElement('span');
    mk.textContent = '\u00a0';
    mk.style.cssText = 'font:inherit;line-height:inherit;visibility:hidden';
    el.appendChild(mk);
    const mr = mk.getBoundingClientRect();
    mk.remove();
    out.push({
      text: (el.textContent || '').trim().slice(0, 14),
      cls: el.className.trim().slice(0, 60),
      display: cs.display, parentDisplay: parent,
      h: +r.height.toFixed(2), minHeight: cs.minHeight, lineHeight: cs.lineHeight,
      lineOff: +(((mr.top - r.top) - (r.bottom - mr.bottom)) / 2).toFixed(2),
    });
  });
  return out;
})()`;

class Session {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map();
    ws.addEventListener('message', ev => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) { this.pending.get(m.id)(m); this.pending.delete(m.id); }
    });
  }
  call(method, params = {}) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, m => m.error ? rej(new Error(method + ': ' + JSON.stringify(m.error))) : res(m.result || {}));
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async eval(expression) {
    const r = await this.call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }
}

(async () => {
  const target = await (await fetch(`${DEBUG}/json/new?` + encodeURIComponent('about:blank'), { method: 'PUT' })).json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.addEventListener('open', res); ws.addEventListener('error', rej); });
  const s = new Session(ws);
  await s.call('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await s.call('Page.enable');
  await s.call('Page.navigate', { url });
  await new Promise(r => setTimeout(r, 1800));

  const rows = await s.eval(MEASURE(selector));
  const bad = rows.filter(x => Math.abs(x.lineOff) > 0.6);
  console.log(`${url}\n  measured ${rows.length} controls; |lineOff| > 0.6px: ${bad.length}`);
  console.log(`  computed display counts: ${JSON.stringify(rows.reduce((a, x) => (a[x.display] = (a[x.display] || 0) + 1, a), {}))}`);
  if (bad.length) {
    console.log('  offenders:');
    bad.sort((a, b) => Math.abs(b.lineOff) - Math.abs(a.lineOff)).slice(0, 20)
      .forEach(x => console.log(`    off=${x.lineOff}px  h=${x.h} minH=${x.minHeight} lh=${x.lineHeight} display=${x.display} parent=${x.parentDisplay}  "${x.text}"  ${x.cls}`));
    console.log('  A constant offset that tracks min-height minus (padding + line-height) is the blockified-inline-block bug: put display:inline-flex;align-items:center;justify-content:center on the control class.');
  }
  try { await fetch(`${DEBUG}/json/close/${target.id}`, { method: 'PUT' }); } catch (_) {}
  process.exit(bad.length ? 1 : 0);
})();
