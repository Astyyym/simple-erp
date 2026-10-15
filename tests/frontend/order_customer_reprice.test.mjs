import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

/* 「单价跟着客户走」的回归（2026-10-15）。

   实测到的缺陷（隔离库：同一商品档案默认价 100、客户甲成交 80、客户乙成交 85）：
   `refineProductRow()` 只在**点选商品那一刻**读一次 hidden `customer_id`，于是
     ① 先点商品、后选客户 → 单价停在 100，不带出 80；
     ② 已填行后换客户 / 重编辑换客户 → 单价仍是不动的那个数。
   修复是两层：共享组件给出 `onCounterpartyChanged()`（hidden 赋值不触发任何事件，
   所以用值指纹轮询兜底），销售/退货页据此将已命中档案的行按新客户重新取价。
   拿货/退拿货页反向：单价是入库成本，**不再**借用客户价接口。 */

const shared = fs.readFileSync(new URL('../../app/erp/static/order-entry-common.js', import.meta.url), 'utf8');
const salesPage = fs.readFileSync(new URL('../../app/erp/templates/orders/new.html', import.meta.url), 'utf8');

function slice(source, from, to, label) {
  const start = source.indexOf(from);
  const end = source.indexOf(to, start + from.length);
  assert(start >= 0 && end > start, `无法定位代码片段：${label}`);
  return source.slice(start, end);
}

const wait = ms => new Promise(resolve => { setTimeout(resolve, ms); });

test('往来对象被脚本赋值后，onCounterpartyChanged 会回调新值（值没变则不回调）', async () => {
  const code = slice(shared, 'function onCounterpartyChanged(', '\n  /* 关联原单后对齐', 'onCounterpartyChanged');
  const timers = [];
  const calls = [];
  const context = vm.createContext({
    String,
    window: { setInterval: fn => { timers.push(fn); return timers.length; } },
    document: { getElementById: () => context.input },
    onCommit: value => calls.push(value),
    input: { value: '', addEventListener() {} },
  });
  vm.runInContext(`${code}; this.commit = onCounterpartyChanged(input, onCommit);`, context);
  const commit = vm.runInContext('this.commit', context);
  const input = context.input;

  assert.deepEqual(calls, [], '初始值不应触发回调');
  input.value = '7';
  commit();                       // 显式提交路径（关联原单带出对象等）
  assert.deepEqual(calls, ['7'], '显式提交没有回调新对象 id');
  input.value = '9';
  timers.forEach(fn => fn());     // 脚本直接赋值 hidden：没有任何事件，只能靠轮询一拍
  assert.deepEqual(calls, ['7', '9'], '轮询没有发现往来对象变化');
  timers.forEach(fn => fn());
  assert.equal(calls.length, 2, '对象没变却反复回调，会不停重取价');
});

test('销售/退货页：换客户按新客户重写已命中档案的行，原单带入行除外', () => {
  const code = slice(salesPage, 'function repriceRowsForCustomer(', '\nErpOrderEntry.onCounterpartyChanged(', 'repriceRowsForCustomer');
  assert.ok(!code.includes('{{'), 'repriceRowsForCustomer 片段里不应含 Jinja 表达式');

  const rows = {
    normal: { '.product-id': { value: '11' }, '.picker-name-input': { value: '软密封闸阀DN100' }, '.source-item-id': { value: '' } },
    sourceApplied: { '.product-id': { value: '11' }, '.picker-name-input': { value: '软密封闸阀DN100' }, '.source-item-id': { value: '77' } },
    blank: { '.product-id': { value: '' }, '.picker-name-input': { value: '' }, '.source-item-id': { value: '' } },
  };
  const makeTr = name => ({
    querySelector: selector => {
      if (selector === '.product-id' || selector === '.picker-name-input' || selector === '.source-item-id') {
        const field = rows[name][selector];
        return field ? { get value() { return field.value; } } : null;
      }
      return null;
    },
  });

  const repriced = [];
  // `repriceRowsForCustomer` 引用页面级 `sourceTraceOnly`（切片之外），沙箱里补上。
  const context = vm.createContext({
    document: { querySelectorAll: () => [makeTr('normal'), makeTr('sourceApplied'), makeTr('blank')] },
    productCatalog: [{ id: 11, name: '软密封闸阀 DN100', unit: '台', default_price_cents: 420000 }],
    Number, String,
    sourceTraceOnly: false,
    refineProductRow: (tr, product) => { repriced.push(product); },
  });
  vm.runInContext(code, context);
  vm.runInContext('repriceRowsForCustomer()', context);

  assert.equal(repriced.length, 1, '只有「已命中档案且非原单带入」的行该被重取价');
  assert.equal(repriced[0].id, 11);
  // 规范名取自商品档案（内嵌目录），而不是输入框里用户可能改过的写法。
  assert.equal(repriced[0].name, '软密封闸阀 DN100', '重取价用的查询串应来自商品档案规范名');
});

test('原单「仅追溯」关联时，带入行也随客户重取价（成本齐备时不重算）', () => {
  const code = slice(salesPage, 'function repriceRowsForCustomer(', '\nErpOrderEntry.onCounterpartyChanged(', 'repriceRowsForCustomer');
  const rows = {
    sourceApplied: { '.product-id': { value: '11' }, '.picker-name-input': { value: '软密封闸阀DN100' }, '.source-item-id': { value: '77' } },
  };
  const makeTr = name => ({
    querySelector: selector => (rows[name][selector] ? { get value() { return rows[name][selector].value; } } : null),
  });
  const repriced = [];
  const context = vm.createContext({
    document: { querySelectorAll: () => [makeTr('sourceApplied')] },
    productCatalog: [{ id: 11, name: '软密封闸阀 DN100' }],
    Number, String,
    sourceTraceOnly: true,   // 来源单缺成本 → 只作追溯，带入行允许随客户重取价
    refineProductRow: (tr, product) => { repriced.push(product); },
  });
  vm.runInContext(code, context);
  vm.runInContext('repriceRowsForCustomer()', context);
  assert.equal(repriced.length, 1, '仅追溯关联时，带入行也应随客户重取价');
});

test('销售/退货页把往来对象变化接到重取价，且共享 JS 版本已提升', () => {
  assert.match(salesPage, /ErpOrderEntry\.onCounterpartyChanged\(document\.getElementById\('customer_id'\), repriceRowsForCustomer\)/,
    '销售/退货页没有订阅往来对象变化');
  assert.match(salesPage, /order-entry-common\.js[^"]*\?v=3/, '共享 JS 改了却仍带旧版本号，浏览器/WebView2 会用缓存旧文件');
});

test('拿货/退拿货页的单价候选不再借用客户价接口', () => {
  ['purchases/new.html', 'purchases/return_new.html'].forEach(file => {
    const source = fs.readFileSync(new URL(`../../app/erp/templates/${file}`, import.meta.url), 'utf8');
    assert.ok(!source.includes('/orders/api/products'), `${file}：仍借用客户价接口取拿货单价`);
    assert.ok(!source.includes('is_customer_price'), `${file}：拿货单价仍出现客户价分支`);
    assert.ok(source.includes('default_price_cents'), `${file}：常规价应改用内嵌商品目录的档案价`);
  });
});
