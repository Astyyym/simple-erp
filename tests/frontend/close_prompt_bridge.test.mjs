import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

/* 关闭浮层 → pywebview 桥的调用契约（2026-11-25 用户实测缺陷的护栏）。
 *
 * 真实缺陷：`callBridge(name, arg)` 写的是 `api[name](arg)`，arg 是 undefined 时
 * pywebview 把 arguments 原样转发成 Python 的 `func(*params)` → `func(None)` →
 * TypeError → Promise reject；而 `api[name](arg)` 是同步返回 Promise、不抛，
 * 同步 try/catch 抓不到 —— 用户看到的就是「点了『我已同步，关闭软件』窗口没关、
 * 也没有任何提示」，只能自己再点一次窗口的 ×。
 * 真窗口探针（pywebview 6.2.1 + WebView2）复现过：`Api.no_arg(undefined)` → REJECTED。
 *
 * 所以这里锁三条：
 * 1. 点主按钮必须**零参数**调用 `erp_close_window`（不能多塞 undefined）；
 * 2. 调用失败（Promise reject）必须留下可见提示，不能再静默无反应；
 * 3. 桥根本不在时同样要提示。
 */

const SRC = fs.readFileSync(new URL('../../app/erp/static/js/close-prompt.js', import.meta.url), 'utf8');

function makeNode(tag = 'div') {
  const node = {
    tagName: tag,
    id: '',
    className: '',
    style: { cssText: '' },
    children: [],
    listeners: {},
    attributes: {},
    innerHTML: '',
    textContent: '',
    parentNode: null,
    classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); },
    removeAttribute(name) { delete this.attributes[name]; },
    addEventListener(type, handler) { this.listeners[type] = handler; },
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    removeChild(child) { this.children = this.children.filter(item => item !== child); child.parentNode = null; return child; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    focus() {},
    matches() { return false; },
    scrollIntoView() {},
  };
  return node;
}

/** 最小 DOM：任何 getElementById 都自动建一个节点，浮层里的按钮按选择器预先挂好。 */
function makeDocument() {
  const registry = new Map();
  const byId = id => {
    if (!registry.has(id)) {
      const node = makeNode();
      node.id = id;
      registry.set(id, node);
    }
    return registry.get(id);
  };
  const buttons = {
    '#erpCloseCancel': byId('erpCloseCancel'),
    '#erpCloseAnyway': byId('erpCloseAnyway'),
    '#erpCloseNow': byId('erpCloseNow'),
  };
  const body = makeNode('body');
  const head = makeNode('head');
  body.appendChild = function (child) {
    child.parentNode = this;
    this.children.push(child);
    if (child.id) registry.set(child.id, child);
    return child;
  };
  return {
    body,
    head,
    registry,
    buttons,
    getElementById: id => registry.get(id) || null,
    querySelector: selector => (selector === 'meta[name="erp-close-state-url"]' ? null : null),
    createElement: tag => {
      const node = makeNode(tag);
      if (tag === 'div') {
        // 浮层本体：querySelector 按 #id 返回预先造好的按钮
        node.querySelector = selector => buttons[selector] || null;
      }
      return node;
    },
  };
}

function load({ api }) {
  const document = makeDocument();
  const calls = [];
  const timers = [];
  const context = {
    document,
    console: { error() {}, log() {}, warn() {} },
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout() {},
    setInterval: () => 1,
    clearInterval() {},
    fetch: async () => ({ ok: true, json: async () => ({ ok: true, listening: true, devices: [], last_sync_seconds: 5, last_sync_label: '刚刚', url: '', qr: '' }) }),
    Promise,
  };
  if (api) {
    context.pywebview = { api };
  }
  context.window = context;
  vm.createContext(context);
  vm.runInContext(SRC, context);
  return { context, document, calls, timers };
}

function recordingApi({ fail = false } = {}) {
  const calls = [];
  const record = name => function (...args) {
    calls.push({ name, argc: args.length });
    return fail ? Promise.reject(new Error('boom')) : Promise.resolve(true);
  };
  return { calls, api: { erp_close_window: record('erp_close_window'), erp_close_prompt_cancel: record('erp_close_prompt_cancel') } };
}

const flush = () => new Promise(resolve => setImmediate(resolve));

test('点「我已同步，关闭软件」= 零参数调用 erp_close_window（不能再多塞 undefined）', async () => {
  const { calls, api } = recordingApi();
  const { context, document } = load({ api });
  assert.equal(context.window.erpClosePrompt(), true, '浮层应同步返回 true');

  document.buttons['#erpCloseNow'].listeners.click();
  await flush();

  assert.deepEqual(calls, [{ name: 'erp_close_window', argc: 0 }],
    '桥调用必须零参数：多传一个 undefined 会让 Python 抛 TypeError、窗口关不掉');
});

test('点「取消，先不关」也是零参数调用，否则「每次必弹」会失效', async () => {
  const { calls, api } = recordingApi();
  const { context, document } = load({ api });
  context.window.erpClosePrompt();

  document.buttons['#erpCloseCancel'].listeners.click();
  await flush();

  assert.deepEqual(calls, [{ name: 'erp_close_prompt_cancel', argc: 0 }]);
});

test('桥调用失败必须在页面上留下可见提示，不能静默无反应', async () => {
  const { api } = recordingApi({ fail: true });
  const { context, document } = load({ api });
  context.window.erpClosePrompt();

  document.buttons['#erpCloseNow'].listeners.click();
  await flush();
  await flush();

  const hint = document.getElementById('erpCloseBridgeHint');
  assert.ok(hint, '关不掉时必须告诉用户去点窗口右上角的 ×，否则就是「点了没反应」');
  assert.match(hint.textContent, /×/);
});

test('没有 pywebview 桥（浏览器直接访问）时同样给可见提示', () => {
  const { context, document } = load({ api: null });
  context.window.erpClosePrompt();

  document.buttons['#erpCloseNow'].listeners.click();

  assert.ok(document.getElementById('erpCloseBridgeHint'));
});
