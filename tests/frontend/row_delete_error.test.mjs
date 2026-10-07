import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// 回归背景（2026-10-07 用户反馈）：行内删除失败时曾固定弹「删除失败，请刷新页面后重试」，
// 把后端的真实原因吞掉；后端则把中文原因当纯文本返回，浏览器直接显示成一行裸文字。
// 现在改为：fetch 拿 JSON 里的 error，就地插入提示条。本文件真跑 base.html 里那段 JS。

const source = fs.readFileSync(new URL('../../app/erp/templates/base.html', import.meta.url), 'utf8');
const start = source.indexOf('async function postAndRemoveRow(');
const end = source.indexOf('function toggleAll(');
assert(start >= 0 && end > start, 'base.html must keep postAndRemoveRow + showRowError');
const script = source.slice(start, end);

class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.parent = null;
    this.attrs = {};
    this.textContent = '';
    this.className = '';
    this.listeners = new Map();
    this.classList = {
      _set: new Set(),
      add: c => this.classList._set.add(c),
      remove: c => this.classList._set.delete(c),
      contains: c => this.classList._set.has(c),
    };
  }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  append(...nodes) { nodes.forEach(n => this.appendChild(n)); return this; }
  get parentNode() { return this.parent; }
  insertBefore(node, ref) {
    const idx = ref ? this.children.indexOf(ref) : -1;
    node.parent = this;
    if (idx < 0) this.children.push(node); else this.children.splice(idx, 0, node);
    return node;
  }
  replaceChildren() { this.children = []; return this; }
  setAttribute(k, v) { this.attrs[k] = v; if (k === 'id') this.id = v; }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(t, h) { if (!this.listeners.has(t)) this.listeners.set(t, []); this.listeners.get(t).push(h); }
  dispatch(t) { for (const h of this.listeners.get(t) || []) h({ target: this }); }
  closest(tag) { for (let n = this; n; n = n.parent) if (n.tag === tag) return n; return null; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); }
  scrollIntoView() {}
}

function build({ ok, status, body, jsonThrows = false }) {
  const table = new El('table');
  const body_ = new El('tbody');
  const tr = new El('tr');
  const form = new El('form');
  form.action = '/orders/999999/delete';
  form.closest = tag => (tag === 'tr' ? tr : null);
  tr.closest = tag => (tag === 'table' ? table : null);
  tr.appendChild(form);
  body_.appendChild(tr);
  table.appendChild(body_);
  const container = new El('div');
  container.appendChild(table);

  const created = [];
  const document = {
    body: container,
    getElementById: id => created.find(n => n.id === id || n.attrs.id === id) || null,
    createElement: tag => { const el = new El(tag); created.push(el); return el; },
    querySelector: sel => (sel === 'table' ? table : null),
    addEventListener: () => {},
  };

  const calls = [];
  const response = {
    ok,
    status,
    json: async () => { if (jsonThrows) throw new Error('not json'); return body; },
  };
  const context = vm.createContext({
    document,
    FormData: class { constructor() {} },
    fetch: async (url, init) => { calls.push({ url, headers: init.headers }); return response; },
  });
  vm.runInContext(script, context);
  return { context, tr, table, container, created, calls };
}

test('失败时就地显示后端返回的真实原因，而不是固定文案', async () => {
  const { context, tr, created } = build({ ok: false, status: 400, body: { error: '订单不是该商品的最后一笔库存业务' } });
  await context.postAndRemoveRow(tr.children[0]);
  const box = created.find(n => n.id === 'rowActionError' || n.attrs.id === 'rowActionError');
  assert.ok(box, '应该插入提示条');
  const text = box.children.find(c => c.className === 'row-action-error-text');
  assert.equal(text.textContent, '订单不是该商品的最后一笔库存业务');
});

test('失败时不再使用 alert，且行不会从表里消失', async () => {
  const { context, tr, table, created } = build({ ok: false, status: 400, body: { error: '原因X' } });
  await context.postAndRemoveRow(tr.children[0]);
  // 行仍在（未被误删）
  assert.equal(table.children[0].children.length, 1);
  // 提示条是就地插入的，不是弹窗
  assert.ok(created.some(n => n.id === 'rowActionError' || n.attrs.id === 'rowActionError'));
});

test('后端返回非 JSON 时给出 HTTP 状态而不是静默', async () => {
  const { context, tr, created } = build({ ok: false, status: 500, body: null, jsonThrows: true });
  await context.postAndRemoveRow(tr.children[0]);
  const box = created.find(n => n.id === 'rowActionError' || n.attrs.id === 'rowActionError');
  const text = box.children.find(c => c.className === 'row-action-error-text');
  assert.match(text.textContent, /HTTP 500/);
});

test('成功时移除该行，且不插入提示条', async () => {
  const { context, tr, table, created } = build({ ok: true, status: 204, body: null });
  await context.postAndRemoveRow(tr.children[0]);
  assert.equal(table.children[0].children.length, 0, '成功后行应被移除');
  assert.equal(created.find(n => n.id === 'rowActionError' || n.attrs.id === 'rowActionError'), undefined);
});

test('请求带 X-Requested-With: fetch（后端据此返回 JSON）', async () => {
  const { context, tr, calls } = build({ ok: true, status: 204, body: null });
  await context.postAndRemoveRow(tr.children[0]);
  assert.equal(calls[0].headers['X-Requested-With'], 'fetch');
});

test('提示条有关闭按钮，点了就移除', async () => {
  const { context, tr, created, container } = build({ ok: false, status: 400, body: { error: '原因Y' } });
  await context.postAndRemoveRow(tr.children[0]);
  const box = created.find(n => n.id === 'rowActionError' || n.attrs.id === 'rowActionError');
  const close = box.children.find(c => c.className === 'row-action-error-close');
  assert.ok(close, '应该有关闭按钮');
  close.dispatch('click');
  assert.equal(container.children.includes(box), false, '点关闭后提示条应移除');
});
