import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

/* 数量留空后的安全网：`ErpOrderEntry.findRowMissingQuantity()`。
 *
 * 背景（2026-11-25 用户口径）：数量不再预填 1。代价是「填了商品、忘了填数量」时，
 * 后端 `_typed_rows_from_form()` 只收「有名称 **且** 有数量」的行，那一行会被
 * **静默丢掉** —— 单据照样保存，只是少一行、金额少算，单子上看不出来。
 * 所以保存前必须能找出这种行。这个测试锁它的判定口径：
 * - 有商品名 + 数量空 → 命中（要拦）
 * - 有商品名 + 数量有值 → 不命中
 * - 整行没商品（空行）→ 不命中（那是空行，不是漏填）
 * - 换页提示行（.slip-notice）→ 不算录入行
 */

const SRC = fs.readFileSync(new URL('../../app/erp/static/order-entry-common.js', import.meta.url), 'utf8');

function cell(value) {
  return { value };
}

function row({ name = '', qty = '', notice = false } = {}) {
  const cells = {
    '.product-name': cell(name),
    '.picker-name-input': cell(name),
    '.quantity': cell(qty),
  };
  return {
    notice,
    querySelector: selector => cells[selector] || null,
  };
}

function makeTbody(rows) {
  return {
    querySelectorAll: selector => {
      assert.equal(selector, 'tr:not(.slip-notice)', '只认录入行，换页提示行不算');
      return rows.filter(item => !item.notice);
    },
  };
}

function load() {
  const context = { console, window: {} };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(SRC, context);
  return context.window.ErpOrderEntry;
}

test('有商品名但数量为空 → 命中（保存前要拦下并定位到那一格）', () => {
  const ErpOrderEntry = load();
  const target = row({ name: '软密封闸阀DN100', qty: '' });
  const tbody = makeTbody([row({ name: '蝶阀DN80', qty: '2' }), target]);

  const found = ErpOrderEntry.findRowMissingQuantity(tbody);

  assert.ok(found, '漏填数量的行没被找出来，保存后会静默丢行');
  assert.equal(found.row, target);
  assert.equal(found.input, target.querySelector('.quantity'));
});

test('数量都有值 → 不拦', () => {
  const ErpOrderEntry = load();
  assert.equal(ErpOrderEntry.findRowMissingQuantity(makeTbody([row({ name: '闸阀', qty: '3' })])), null);
});

test('整行没填商品（空行）→ 不算漏填数量', () => {
  const ErpOrderEntry = load();
  assert.equal(ErpOrderEntry.findRowMissingQuantity(makeTbody([row({ name: '', qty: '' })])), null);
});

test('换页提示行不参与判定', () => {
  const ErpOrderEntry = load();
  const tbody = makeTbody([row({ name: '', qty: '', notice: true })]);
  assert.equal(ErpOrderEntry.findRowMissingQuantity(tbody), null);
});

test('数量只有空白字符也算空（用户敲了空格再删）', () => {
  const ErpOrderEntry = load();
  assert.ok(ErpOrderEntry.findRowMissingQuantity(makeTbody([row({ name: '闸阀', qty: '  ' })])));
});

test('tbody 不存在时不炸', () => {
  const ErpOrderEntry = load();
  assert.equal(ErpOrderEntry.findRowMissingQuantity(null), null);
});
