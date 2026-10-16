import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

/* 四类开单页「点选商品候选 → 行内写入单位」的回归。
 *
 * 背景（2026-10-14 用户实测）：销售单/退货单点选候选后下拉不关、单位与单价不带入。
 * 根因是 `applyProductToRow()` 里写了 P2 已删除的双控件 `unitSelect`（变量不存在）→
 * ReferenceError 抛在 `choose()` 的 onPick 里，连带跳过关闭浮层 / 写单位 / recalc。
 * 所以这里直接把页面真实的 `bindRow()` 放进 vm 跑，用 ErpProductPicker 桩捕获 onPick 回调，
 * 调它一次：**既断言单位写入，也断言不抛错**（后者才是这个 bug 的直接指纹）。
 *
 * 2026-10-15 默认价下线后：点选商品**不再写单价**（旧版无条件写档案默认价）。
 * 销售/退货的单价由 `refineProductRow()` 按当前客户的专属价异步带出，没有专属价就留空；
 * 拿货/退拿货的单价本来就是手输成本，从来没有自动写入。故三页一律断言单价保持为空。
 */

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

function fixture({ file, from, to, selectors, globals }) {
  const pageSource = fs.readFileSync(new URL(`../../app/erp/templates/${file}`, import.meta.url), 'utf8');
  const code = slice(pageSource, from, to);
  assert(!code.includes('{{'), `${file} 的 bindRow 片段里不应含 Jinja 表达式`);

  const inputs = {};
  selectors.forEach(selector => { inputs[selector] = stubInput(); });
  const row = {
    dataset: {},
    querySelector: selector => inputs[selector] || null,
    querySelectorAll: () => [inputs['.quantity'], inputs['.unit-price']].filter(Boolean),
  };
  let onPick = null;
  const context = vm.createContext({
    document: { getElementById: () => stubInput('0') },
    // 点选候选的真实入口是**页面级** onPick（`picker.set(product); applyProduct(product);` 两句），
    // 所以捕获 bindAutocomplete 的第 5 个参数，而不是 mountHybrid 的那个。
    ErpOrderEntry: { bindUnitCell() {}, bindAutocomplete(_input, _box, _load, _render, callback) { onPick = callback; } },
    ErpProductPicker: { mountHybrid(row) {
      // 忠实一点：真实 mountHybrid 的 set() 就是写这两个字段（点选候选不触发 input 事件）。
      return {
        set(product) {
          const nameField = row.querySelector('.picker-name-input');
          const idField = row.querySelector('.product-id');
          if (nameField) nameField.value = product.name || '';
          if (idField) idField.value = product.id;
        },
        reset() {},
      };
    } },
    productCatalog: [],
    catalog: [],
    catalogById: new Map(),
    fetchJson: async () => [],
    recalc() {},
    clearMissingHint() {},
    refineProductRow() {},
    ...globals,
  });
  vm.runInContext(code, context);
  context.bindRow(row);
  return { row, inputs, pick: product => onPick(product) };
}

const ORDERS_SELECTORS = ['.unit', '.unit-suggestions', '.product-id', '.product-name', '.unit-price',
  '.regular-price-option', '.regular-price-check', '.regular-price-text', '.picker-name-input',
  '.product-suggestions', '.quantity'];

const PURCHASE_SELECTORS = ['.unit', '.unit-suggestions', '.product-id', '.picker-missing', '.price-quick',
  '.line-total', '.picker-name-input', '.product-suggestions', '.quantity', '.unit-price'];

const PAGES = [
  // 差异（业务事实，不是遗漏）：
  // - 销售/退货：单价由 refineProductRow 按当前客户的专属价异步带出（点选那一刻不写），
  //   商品名另写进提交用的 .product-name。
  // - 拿货/退拿货：单价是**拿货成本**，点选不自动填（2026-10-15 起连「常规价」按钮也没有了）；
  //   商品名由 picker.set() 写进输入框，页面不另设 .product-name。
  { label: '销售/退货单', file: 'orders/new.html', from: 'function bindRow(tr) {',
    to: "document.querySelectorAll('#orderBody tr:not(.slip-notice)').forEach(bindRow);",
    selectors: ORDERS_SELECTORS, hasProductNameField: true },
  { label: '拿货单', file: 'purchases/new.html', from: 'function bindRow(tr) {',
    to: "document.querySelectorAll('#purchaseRows tr:not(.slip-notice)').forEach(bindRow);",
    selectors: PURCHASE_SELECTORS, hasProductNameField: false },
  { label: '退拿货单', file: 'purchases/return_new.html', from: 'function bindRow(tr) {',
    to: "document.querySelectorAll('#returnRows tr:not(.slip-notice)').forEach(bindRow);",
    selectors: PURCHASE_SELECTORS, hasProductNameField: false },
];

PAGES.forEach(({ label, file, from, to, selectors, hasProductNameField }) => {
  test(`${label}：点选商品候选后单位写入行内，且不抛错`, () => {
    const { row, inputs, pick } = fixture({ file, from, to, selectors });
    assert.doesNotThrow(
      () => pick({ id: 13, name: '软密封闸阀DN100', unit: '台' }),
      `${label}：applyProduct 抛错会让候选浮层关不掉、单位留在空值`,
    );
    assert.equal(inputs['.unit'].value, '台', `${label}：单位没有带到行内`);
    assert.equal(inputs['.product-id'].value, 13, `${label}：商品 id 没有写入`);
    assert.equal(inputs['.picker-name-input'].value, '软密封闸阀DN100', `${label}：商品名没有写入`);
    if (hasProductNameField) {
      assert.equal(inputs['.product-name'].value, '软密封闸阀DN100', `${label}：提交用的 product_name 没有写入`);
    }
    // 点选商品不再写单价：售价只来自客户专属价（refineProductRow 异步带出）。
    assert.equal(inputs['.unit-price'].value, '', `${label}：点选商品不应再写单价`);
    assert.equal(row.dataset.unitCost, undefined);
  });
});

test('四类开单页都不再引用已删除的单位双控件标识', () => {
  PAGES.forEach(({ label, file }) => {
    const pageSource = fs.readFileSync(new URL(`../../app/erp/templates/${file}`, import.meta.url), 'utf8');
    assert.ok(!pageSource.includes('unitSelect'), `${label}：仍引用 unitSelect`);
    assert.ok(!pageSource.includes('unit-select'), `${label}：仍引用 unit-select`);
  });
});
