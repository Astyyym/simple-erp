import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
const moduleUrl = new URL('../../app/erp/static/page-refresh.mjs', import.meta.url);

test('the shared in-place refresh module ships with the app', async () => {
  assert(existsSync(moduleUrl), 'app/erp/static/page-refresh.mjs 缺失');
  const mod = await import(moduleUrl);
  for (const name of ['scrollAnchor', 'refreshInPlace', 'formUrl', 'wireInPlace', 'initPageRefresh']) {
    assert.equal(typeof mod[name], 'function', `${name} 应为函数`);
  }
});

test('scrollAnchor prefers the scrolling pane over the document', async () => {
  const {scrollAnchor} = await import(moduleUrl);
  const pane = {scrollTop: 420};
  assert.deepEqual(scrollAnchor({querySelector: () => pane}), {target: pane, top: 420});
  // 面板没滚动时退回文档级滚动，避免把 0 当成「需要恢复」。
  const scroller = {scrollTop: 55};
  assert.deepEqual(
    scrollAnchor({querySelector: () => ({scrollTop: 0}), scrollingElement: scroller}),
    {target: scroller, top: 55},
  );
});

test('refreshInPlace swaps matching fragments, adds new ones and drops stale ones', async () => {
  const {refreshInPlace} = await import(moduleUrl);
  const container = {
    children: [],
    replaceChild(node, old) {
      this.children = this.children.filter(child => child !== old);
      this.children.push(node);
    },
    removeChild(node) {
      this.children = this.children.filter(child => child !== node);
    },
  };
  const makeNode = id => ({id, parentNode: container, children: []});
  const currentA = makeNode('fragA');
  const currentStale = makeNode('fragStale');
  container.children = [currentA, currentStale];
  const freshA = {id: 'fragA', getAttribute: () => 'data-refresh-fragment'};
  const freshB = {id: 'fragB', getAttribute: () => 'data-refresh-fragment'};
  const pane = {scrollTop: 300};
  const doc = {
    querySelector: () => pane,
    getElementById: id => ({fragA: currentA, fragStale: currentStale}[id] || null),
    querySelectorAll: () => [currentA, currentStale],
    importNode: node => node,
    scrollingElement: {scrollTop: 0},
  };
  const freshPage = {querySelectorAll: () => [freshA, freshB]};
  const originalParser = globalThis.DOMParser;
  globalThis.DOMParser = class { parseFromString() { return freshPage; } };
  try {
    const root = {ownerDocument: doc};
    const replaced = await refreshInPlace(
      root, 'http://x/orders/?customer=1', doc,
      async () => ({ok: true, text: async () => '<html></html>'}),
    );
    // fragA 被换、fragB 旧页无此 id（跳过）、fragStale 被移除 → 2 次变更。
    assert.equal(replaced, 2);
    assert.deepEqual(container.children.map(n => n.id), ['fragA']);
    assert.equal(container.children[0], freshA, 'fragA 应被替换为新节点');
    assert.equal(pane.scrollTop, 300, '替换后滚动位置必须还原');
  } finally {
    globalThis.DOMParser = originalParser;
  }
});

test('refreshInPlace rejects when the response has no refreshable fragment', async () => {
  const {refreshInPlace} = await import(moduleUrl);
  const doc = {
    querySelector: () => null, getElementById: () => null,
    querySelectorAll: () => [], importNode: n => n, scrollingElement: {scrollTop: 0},
  };
  const originalParser = globalThis.DOMParser;
  globalThis.DOMParser = class { parseFromString() { return {querySelectorAll: () => []}; } };
  try {
    await assert.rejects(
      refreshInPlace({ownerDocument: doc}, 'http://x/a', doc,
        async () => ({ok: true, text: async () => '<html></html>'})),
      /未找到可替换/,
    );
  } finally {
    globalThis.DOMParser = originalParser;
  }
});

test('refreshInPlace rejects on a non-ok response so callers can fall back', async () => {
  const {refreshInPlace} = await import(moduleUrl);
  const doc = {querySelector: () => null, getElementById: () => null,
               querySelectorAll: () => [], importNode: n => n, scrollingElement: {scrollTop: 0}};
  await assert.rejects(
    refreshInPlace({ownerDocument: doc}, 'http://x/a', doc, async () => ({ok: false, status: 500})),
    /刷新失败/,
  );
});

test('formUrl drops blank fields and keeps the form action path', async () => {
  const {formUrl} = await import(moduleUrl);
  const form = {
    ownerDocument: {location: {pathname: '/orders/', href: 'http://x/orders/'}},
    getAttribute: name => (name === 'action' ? '/orders/' : null),
  };
  const originalFormData = globalThis.FormData;
  const pairs = [['customer', '甲'], ['start_date', ''], ['order_type', 'sale']];
  globalThis.FormData = class {
    entries() { return pairs[Symbol.iterator](); }
    [Symbol.iterator]() { return pairs[Symbol.iterator](); }
  };
  try {
    const url = new URL(formUrl(form, form.ownerDocument));
    assert.equal(url.pathname, '/orders/');
    assert.equal(url.searchParams.get('customer'), '甲');
    assert.equal(url.searchParams.get('order_type'), 'sale');
    assert.equal(url.searchParams.has('start_date'), false, '空字段不写进 URL');
  } finally {
    globalThis.FormData = originalFormData;
  }
});
