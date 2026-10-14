import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

/* F6（2026-10-14）回归：预览带入必须是「一次性」的。
 *
 * 实测症状：点「按预览带入商品行」两次，明细里出现两套重复行（数量/金额/成本全翻倍）。
 * 根因：点击处理每次跑一遍「写 tbody.rows[0] + 追加 N-1 行」，没有幂等状态；
 * 且无条件复用第 0 行会静默覆盖用户已手输的那一行。
 *
 * F7（2026-10-14 用户二次实测）：带入还必须是「可替换」的。
 * 实测症状：换了一张原单，上一张单带入的行没消失，再点带入又追加一套（用户看到「增量插入」）。
 * 根因：换单只 reset() 放开按钮，从不撤行。现在 createPreviewApply 记录自己插过哪些行，
 * clear() 只撤这些行，手输行一行不动。这里直接跑真实的 createPreviewApply（两页共用同一份）。
 */

const source = fs.readFileSync(new URL('../../app/erp/static/order-entry-common.js', import.meta.url), 'utf8');
const start = source.indexOf('function createPreviewApply(');
const end = source.indexOf('window.ErpOrderEntry = {', start);
assert(start >= 0 && end > start, 'order-entry-common.js 里找不到 createPreviewApply');
const code = source.slice(start, end);

function harness({ firstRowFilled = false } = {}) {
  const rows = [];
  function makeRow(filled, items) {
    const row = { filled, items, remove() { const index = rows.indexOf(row); if (index >= 0) rows.splice(index, 1); } };
    // signature 是「当前值」的活体指纹：行被改动（items 变长）它就变，modifiedRows() 靠这个识别。
    Object.defineProperty(row, 'signature', { get() { return this.items.join('|'); } });
    return row;
  }
  rows.push(makeRow(firstRowFilled, firstRowFilled ? ['用户手输行'] : []));
  const context = vm.createContext({});
  vm.runInContext(code, context);
  const state = context.createPreviewApply({
    tbody: { rows },
    addRow: () => { const row = makeRow(false, []); rows.push(row); return row; },
    isRowEmpty: row => !row.filled,
    fillRow: (row, item) => { row.filled = true; row.items.push(item); },
    signatureOf: row => row.signature,
  });
  return { rows, state };
}

const ITEMS = ['A', 'B', 'C'];

test('带入一次：复用第 0 行，其余新增，每行只填一项', () => {
  const h = harness();
  assert.equal(h.state.apply(ITEMS), true);
  assert.equal(h.rows.length, 3);
  assert.deepEqual(h.rows.map(row => row.items), [['A'], ['B'], ['C']]);
  assert.equal(h.state.applied(), true);
});

test('连点第二次：不再插入（原缺陷是插两套重复行）', () => {
  const h = harness();
  h.state.apply(ITEMS);
  assert.equal(h.state.apply(ITEMS), false, '第二次带入必须被拒绝');
  assert.equal(h.rows.length, 3, '行数不能变');
  assert.deepEqual(h.rows.map(row => row.items), [['A'], ['B'], ['C']], '不能出现重复项');
});

test('第 0 行已有内容时不覆盖，全部新增行', () => {
  const h = harness({ firstRowFilled: true });
  h.state.apply(ITEMS);
  assert.equal(h.rows.length, 4);
  assert.deepEqual(h.rows[0].items, ['用户手输行'], '用户手输的第 0 行不能被覆盖');
  assert.deepEqual(h.rows.slice(1).map(row => row.items), [['A'], ['B'], ['C']]);
});

test('rows()/clear()：换原单时撤掉带入行，手输行一行不动', () => {
  const h = harness({ firstRowFilled: true });
  h.state.apply(ITEMS);
  assert.equal(h.state.rows().length, 3, 'rows() 只报自己带入的行');
  const removed = h.state.clear();
  assert.equal(removed.length, 3);
  assert.deepEqual(h.rows.map(row => row.items), [['用户手输行']], '手输行必须保留');
  assert.equal(h.state.applied(), false, 'clear() 后按钮要重新放开');
  assert.equal(h.state.rows().length, 0, 'clear() 后不再记得旧行');
});

test('clear() 之后带入新原单：是替换而不是叠加（用户实测的「增量插入」）', () => {
  const h = harness();
  h.state.apply(ITEMS);
  h.state.clear();
  assert.equal(h.state.apply(['D']), true);
  assert.equal(h.rows.length, 1, '旧原单的行已撤掉，只剩新原单的 1 行');
  assert.deepEqual(h.rows[0].items, ['D']);
});

test('modifiedRows()：带入后被手工改过的行要能识别（B 方案据此先确认）', () => {
  const h = harness();
  h.state.apply(ITEMS);
  assert.equal(h.state.modifiedRows().length, 0, '原样带入时没有「已改过」的行');
  h.rows[1].items.push('用户又加的一项'); // 模拟手工改动
  const modified = h.state.modifiedRows();
  assert.equal(modified.length, 1);
  assert.equal(modified[0], h.rows[1], '识别出的必须是那一行本身');
});

test('reset() 只放开按钮：不动行、不丢记录（用户拒绝移除时可保留旧行再带入）', () => {
  const h = harness();
  h.state.apply(ITEMS);
  h.state.reset();
  assert.equal(h.state.applied(), false);
  assert.equal(h.rows.length, 3, 'reset() 不能撤行');
  assert.equal(h.state.rows().length, 3, 'reset() 不能丢掉「哪些行是带入的」');
  assert.equal(h.state.apply(['D']), true);
  assert.equal(h.rows.length, 4);
  assert.deepEqual(h.rows.at(-1).items, ['D']);
});

test('空预览不带入', () => {
  const h = harness();
  assert.equal(h.state.apply([]), false);
  assert.equal(h.state.apply(null), false);
  assert.equal(h.rows.length, 1);
  assert.equal(h.state.applied(), false);
});

test('两个开单页都接上了共享的一次性带入、可替换撤行与按钮置灰', () => {
  const pages = [
    ['退货单', fs.readFileSync(new URL('../../app/erp/templates/orders/new.html', import.meta.url), 'utf8')],
    ['退拿货单', fs.readFileSync(new URL('../../app/erp/templates/purchases/return_new.html', import.meta.url), 'utf8')],
  ];
  pages.forEach(([label, html]) => {
    assert.match(html, /ErpOrderEntry\.createPreviewApply\(/, `${label}：没有用共享的一次性带入`);
    assert.match(html, /previewApply\.disabled = done/, `${label}：带入后没有置灰按钮`);
    assert.match(html, /previewApplyState\.reset\(\)/, `${label}：改选/清除后没有重新放开带入`);
    assert.match(html, /clearSourceAssociation\(/, `${label}：没有共用的解除关联逻辑`);
    // F7：换原单必须撤掉上一张带入的行，且只在有手工改动时确认（B 方案）。
    assert.match(html, /previewApplyState\.clear\(\)/, `${label}：换原单没有撤掉上一张带入的行`);
    assert.match(html, /releasePreviousPreview\(/, `${label}：选中原单时没有先收尾上一张带入的行`);
    assert.match(html, /signatureOf:/, `${label}：没有提供「带入值指纹」，无法判断行是否被手工改过`);
    assert.match(html, /modifiedRows\(\)\.length/, `${label}：没有在移除前判断「是否被改过」`);
    // addRow() 靠克隆 tbody.rows[0] 实现：必须先补空行再撤，否则全删光会炸（实测过）。
    assert.match(html, /\.rows\(\)\.length >= \w+DataRows\(tbody\)\.length\) addRow\(\);/, `${label}：撤行前没有先保住模板行`);
    // 旧写法（每次点击都写 rows[0] + addRow）不得回潮
    assert.equal(html.includes('index === 0 ? tbody.rows[0] : addRow()'), false, `${label}：旧的重复插入写法还在`);
  });
});
