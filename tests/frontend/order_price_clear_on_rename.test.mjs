import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

/* 开单页：商品名从「已建档商品」改成「新商品」时，自动带出的单价必须消失。
 *
 * 背景（2026-10-31 用户实测）：先输 ABC（已建档、有专属价 ¥50）→ 价自动出现；
 * 把 C 删掉再改成 ABD（全新商品）→ 旧价还挂在行上，用户得手动删，
 * 删晚一点还会被后端学成 ABD 的专属价（错误数据，比「没填」更糟）。
 *
 * 这里把页面真实的 `refineProductRow()`（自动写价的地方）和 `bindRow()`（改名时清价的地方）
 * 一起放进 vm：前者写入时打「自动价」标记，后者在未命中商品时按标记撤掉。
 * 判据有两条：**系统写的要撤**、**用户手改的不能撤**。
 */

const PAGE = new URL('../../app/erp/templates/orders/new.html', import.meta.url);

function stubInput(value = '') {
  return {
    value,
    innerHTML: '',
    classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
    addEventListener() {},
    focus() {},
    setAttribute() {},
    removeAttribute() {},
    getAttribute() { return null; },
  };
}

function slice(source, from, to) {
  const start = source.indexOf(from);
  const end = source.indexOf(to, start);
  assert(start >= 0 && end > start, `无法定位代码片段：${from}`);
  return source.slice(start, end);
}

const SELECTORS = ['.unit', '.unit-suggestions', '.product-id', '.product-name', '.unit-price',
  '.picker-name-input', '.product-suggestions', '.quantity'];

function build() {
  const pageSource = fs.readFileSync(PAGE, 'utf8');
  const refineCode = slice(pageSource, 'const repriceSeq = new WeakMap();', 'function bindRow(tr) {');
  const bindCode = slice(
    pageSource,
    'function bindRow(tr) {',
    "document.querySelectorAll('#orderBody tr:not(.slip-notice)').forEach(bindRow);",
  );
  assert(!refineCode.includes('{{') && !bindCode.includes('{{'), '片段里不应含 Jinja 表达式');

  const inputs = {};
  SELECTORS.forEach(selector => { inputs[selector] = stubInput(); });
  const row = {
    dataset: {},
    querySelector: selector => inputs[selector] || null,
    querySelectorAll: () => [inputs['.quantity'], inputs['.unit-price']],
  };

  let applyFromPicker = null;
  let pagePick = null;
  const context = vm.createContext({
    // refineProductRow 会读当前客户 id。
    document: { getElementById: () => stubInput('0') },
    ErpOrderEntry: {
      bindUnitCell() {},
      bindAutocomplete(_input, _box, _load, _render, callback) { pagePick = callback; },
    },
    ErpProductPicker: {
      // 真实 mountHybrid 在「输入框内容未命中任何商品」时回调 onPick(null)，
      // 这里把那个回调抓出来，直接模拟「名字被改成新商品」。
      mountHybrid(_row, _catalog, onPick) {
        applyFromPicker = onPick;
        return { set() {}, reset() {} };
      },
    },
    productCatalog: [],
    // 当前客户对 ABC 的专属价是 50.00。
    fetchJson: async () => [{ id: 13, name: 'ABC', unit: '台', unit_price: '50.00' }],
    recalc() {},
  });

  vm.runInContext(refineCode, context);
  vm.runInContext(bindCode, context);
  context.bindRow(row);
  return {
    inputs,
    pick: product => pagePick(product),
    applyProduct: product => applyFromPicker(product),
  };
}

const tick = () => new Promise(resolve => setTimeout(resolve, 5));

test('名称从已建档商品改成新商品时，自动带出的单价必须撤掉', async () => {
  const { inputs, pick, applyProduct } = build();

  pick({ id: 13, name: 'ABC', unit: '台' });
  await tick();
  assert.equal(inputs['.unit-price'].value, '50.00', 'ABC 的专属价应该自动带出');

  // 删掉 C、再补 D：ABD 是全新商品，没有专属价。
  applyProduct(null);
  assert.equal(inputs['.unit-price'].value, '', '名字不再是 ABC 时，系统带出的旧价必须消失');
});

test('用户自己改过的价不会被当成自动价删掉', async () => {
  const { inputs, pick, applyProduct } = build();

  pick({ id: 13, name: 'ABC', unit: '台' });
  await tick();
  inputs['.unit-price'].value = '88.00'; // 用户手动改价

  applyProduct(null);
  assert.equal(inputs['.unit-price'].value, '88.00', '手输的价属于用户，不能替他清空');
});

test('改回原来的商品会重新带出专属价', async () => {
  const { inputs, pick, applyProduct } = build();

  pick({ id: 13, name: 'ABC', unit: '台' });
  await tick();
  applyProduct(null);
  assert.equal(inputs['.unit-price'].value, '');

  // 重新命中 ABC（用户把名字改回去了）
  pick({ id: 13, name: 'ABC', unit: '台' });
  await tick();
  assert.equal(inputs['.unit-price'].value, '50.00', '重新命中同一商品时应再次带出专属价');
});
