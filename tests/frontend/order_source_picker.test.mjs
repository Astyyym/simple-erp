import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

/* F3（2026-10-14）回归：关联单据（原销售单 / 原拿货单）选完之后必须还能改选。
 *
 * 实测症状：选完原单后再点输入框，候选浮层不再打开，用户以为「选完就锁死了」。
 * 两个原因叠加：
 *   1. 候选按钮的 mousedown 被 preventDefault，点选后焦点仍留在输入框 → 再点不会触发 focus；
 *   2. 此时输入框里是已选项的标签，按它过滤只剩它自己。
 * 修法是 `bindAutocomplete` 的 `options.openOnClick`：点击时若「当前值 == 刚选过的标签」就按空查询列全部候选。
 * 这里直接跑真实的 bindAutocomplete，钉住这两个行为。
 */

const source = fs.readFileSync(new URL('../../app/erp/static/order-entry-common.js', import.meta.url), 'utf8');
const start = source.indexOf('function bindAutocomplete(');
const end = source.indexOf('window.ErpOrderEntry = {', start);
assert(start >= 0 && end > start);
const code = source.slice(start, end);

class Input extends EventTarget {
  constructor(value = '') { super(); this.value = value; this.attributes = new Map(); }
  setAttribute(key, value) { this.attributes.set(key, value); }
  removeAttribute(key) { this.attributes.delete(key); }
  getAttribute(key) { return this.attributes.get(key); }
}
class KeyEvent extends Event {
  constructor(type, init = {}) { super(type); this.key = init.key; }
}

const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

function harness({ options = {}, items = [] } = {}) {
  const input = new Input('');
  const classes = new Set(['d-none']);
  let optionNodes = [];
  const requested = [];
  const rendered = [];
  const box = {
    id: 'suggest_box',
    classList: { contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name) },
    hasAttribute: () => false,
    querySelectorAll: () => optionNodes,
  };
  const context = vm.createContext({
    document: { activeElement: input, getElementById: () => box },
    window: { setTimeout: () => {} },
    showSuggestions(_input, _box, list, _render, onPick) {
      rendered.push(list.length);
      optionNodes = list.map((item, index) => ({
        id: `opt_${index}`, setAttribute() {}, scrollIntoView() {}, click() { onPick(item); },
      }));
      _box.classList.remove('d-none');
      _input.setAttribute('aria-expanded', 'true');
    },
    closeSuggestions(_input, _box) { _box.classList.add('d-none'); _input.setAttribute('aria-expanded', 'false'); },
  });
  vm.runInContext(code, context);
  context.bindAutocomplete(
    input, box,
    async query => { requested.push(query); return items; },
    () => 'option', () => {}, () => {}, '无匹配项', options,
  );
  return { input, box, requested, rendered, isOpen: () => !classes.has('d-none') };
}

async function pickFirst(h) {
  h.input.value = 'MD1';
  h.input.dispatchEvent(new Event('input'));
  await settle();
  h.input.dispatchEvent(new KeyEvent('keydown', { key: 'ArrowDown' }));
  h.input.dispatchEvent(new KeyEvent('keydown', { key: 'Enter' }));
  await settle();
}

test('已选中后再点输入框：按空查询列出全部候选，而不是按已选标签过滤', async () => {
  const h = harness({ options: { listOnEmpty: true, openOnClick: true }, items: [{ order_no: 'MD1' }, { order_no: 'MD2' }] });
  await pickFirst(h);
  assert.equal(h.isOpen(), false, '选中后候选应关闭');

  h.requested.length = 0;
  h.input.dispatchEvent(new Event('click'));
  await settle();
  assert.deepEqual(h.requested, [''], '再次点击应按空查询拉候选（按已选标签过滤只会剩它自己）');
  assert.equal(h.isOpen(), true, '再次点击必须重新打开候选浮层');
});

test('输入了别的关键字时，点击仍按当前输入过滤', async () => {
  const h = harness({ options: { listOnEmpty: true, openOnClick: true }, items: [{ order_no: 'MD1' }] });
  await pickFirst(h);
  h.requested.length = 0;
  h.input.value = 'MD9';
  h.input.dispatchEvent(new Event('click'));
  await settle();
  assert.deepEqual(h.requested, ['MD9'], '用户改了输入就应按输入过滤');
});

test('真人点击顺序（focus 的请求先回来）第一次点击就按空查询列全部候选', async () => {
  /* F5（2026-10-14）实测缺陷：真人点击是 mousedown→focus→mouseup→click，
     focus 触发的请求（本地接口几毫秒）通常在人松开鼠标之前就回来了 →
     浮层已开、click 早退，用户看到「只有已选那一张」；接口慢时又正常（时好时坏）。
     修法：focus 与 click 共用同一条「刚选过的标签 → 空查询」规则。 */
  const h = harness({ options: { listOnEmpty: true, openOnClick: true }, items: [{ order_no: 'MD1' }, { order_no: 'MD2' }] });
  await pickFirst(h);
  h.requested.length = 0;
  h.input.dispatchEvent(new Event('focus'));   // mousedown 后浏览器补发 focus
  await settle();                              // 本地接口先返回，浮层已打开
  h.input.dispatchEvent(new Event('click'));   // mouseup 后 click
  await settle();
  assert.deepEqual(h.requested, [''], '第一次点击就必须按空查询（否则只列出已选那一张）');
  assert.equal(h.isOpen(), true, '浮层应打开');
  assert.equal(h.rendered.at(-1), 2, '应列出全部候选，而不是只有已选那一张');
});

test('未开启 openOnClick 的字段：focus 仍按当前值过滤（行为不变）', async () => {
  const h = harness({ options: { listOnEmpty: true }, items: [{ order_no: 'MD1' }] });
  await pickFirst(h);
  h.requested.length = 0;
  h.input.dispatchEvent(new Event('focus'));
  await settle();
  assert.deepEqual(h.requested, ['MD1'], '客户/商品这类字段保持原行为');
});

test('未开启 openOnClick 的字段保持原行为：点击不额外发请求', async () => {
  const h = harness({ options: { listOnEmpty: true }, items: [{ order_no: 'MD1' }] });
  await pickFirst(h);
  h.requested.length = 0;
  h.input.dispatchEvent(new Event('click'));
  await settle();
  assert.deepEqual(h.requested, [], '客户/商品这类高频字段不应因为一次点击多打一次请求');
});

test('两个关联页都接上了 openOnClick 与共享的往来对象对齐', () => {
  const returnNew = fs.readFileSync(new URL('../../app/erp/templates/orders/new.html', import.meta.url), 'utf8');
  const purchaseReturn = fs.readFileSync(new URL('../../app/erp/templates/purchases/return_new.html', import.meta.url), 'utf8');
  [['退货单', returnNew], ['退拿货单', purchaseReturn]].forEach(([label, html]) => {
    assert.match(html, /openOnClick: true/, `${label}：来源输入框没有开启 openOnClick`);
    assert.match(html, /ErpOrderEntry\.syncCounterparty\(/, `${label}：没有按原单对齐往来对象`);
    assert.match(html, /id="[a-zA-Z]*[Ss]ourceClear"/, `${label}：缺少显式「清除关联」入口`);
  });
  assert.ok(!purchaseReturn.includes('purchaseSourceSelect'), '退拿货单不应再是原生 <select> 来源选择器');
  assert.ok(!returnNew.includes('returnSourceSelect'), '退货单不应退回原生 <select> 来源选择器');
});
