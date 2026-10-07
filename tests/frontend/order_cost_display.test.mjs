import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../app/erp/templates/orders/new.html', import.meta.url), 'utf8');
const start = source.indexOf('function money(');
const end = source.indexOf('function clearAllRows()', start);
const code = source.slice(start, end).replace(/const orderSign = .*?;/, 'const orderSign = 1;');

function page(cost) {
  const inputs = {'.quantity': {value: '2'}, '.unit-price': {value: '25'}, '.product-id': {value: '1'}, '.unit-select': {value: '个'}};
  const outputs = {'.row-no': {}, '.line-total': {}, '.unit-cost-display': {}, '.regular-price-option': {style: {}}};
  const row = {
    dataset: cost === undefined ? {} : {unitCost: cost},
    querySelector: selector => inputs[selector] || outputs[selector],
    querySelectorAll: () => Object.values(inputs),
  };
  const total = {}, preview = {};
  const document = {
    querySelectorAll: () => [row],
    getElementById: id => id === 'grandTotal' ? total : preview,
  };
  const context = vm.createContext({document});
  vm.runInContext(code, context);
  return {context, row, inputs, outputs, total, preview};
}

test('missing cost never becomes a zero-cost/full-margin estimate', () => {
  const p = page();
  p.context.recalc();
  assert.equal(p.outputs['.unit-cost-display'].textContent, '成本未知');
  assert.match(p.preview.textContent, /成本不完整/);
  assert.equal(p.total.textContent, '50.00');
});

test('explicit zero is a known cost, distinct from unavailable', () => {
  const p = page('0.000000');
  p.context.recalc();
  assert.equal(p.outputs['.unit-cost-display'].textContent, '0.00');
  assert.match(p.preview.textContent, /50.00 元/);
  assert.doesNotMatch(p.preview.textContent, /成本不完整/);
});

test('clearing a selected row invalidates cost and resets the readonly cell', () => {
  const p = page('12.500000');
  p.context.recalc();
  assert.equal(p.outputs['.unit-cost-display'].textContent, '12.50');
  p.context.clearRow({closest: () => p.row});
  assert.equal(p.row.dataset.unitCost, undefined);
  assert.equal(p.outputs['.unit-cost-display'].textContent, '未选择商品');
  assert.equal(p.total.textContent, '0.00');
});

test('visible cost rounds to cents while the profit preview keeps unrounded cost', () => {
  const p = page('11.333333');
  p.inputs['.quantity'].value = '3';
  p.context.recalc();
  assert.equal(p.outputs['.unit-cost-display'].textContent, '11.33');
  assert.equal(p.row.dataset.unitCost, '11.333333');
  assert.match(p.preview.textContent, /41.00 元/);
});

test('cost display uses exact half-up cents, including rounding-boundary values', () => {
  const p = page();
  for (const [cost, expected] of [
    ['1.005000', '1.01'], ['0.005000', '0.01'],
    ['1.995000', '2.00'], ['-1.005000', '-1.01'],
    ['-0.000001', '0.00'], ['0.000000', '0.00'],
  ]) assert.equal(p.context.costMoney(cost), expected);
});
