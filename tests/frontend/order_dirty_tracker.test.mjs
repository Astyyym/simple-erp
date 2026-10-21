import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// 未保存提醒的判脏机制（2026-10-21 修复）。
//
// 原缺陷：`let formDirty = false; form.addEventListener('input', () => formDirty = true)`
// 只置位、永不复位 —— 用户把内容全删干净、点「一键清空」清空后，页面明明是空的，
// 切页照样弹「尚未保存」，还会诱导用户确认离开而真的丢掉内容。
// 连带缺陷：单位候选的「×」派发合成 input 事件，一个字没打也把页面判成已修改。
//
// 现在改为实时比对快照。本测试锁住四条行为：
//   1. 值变了 → 脏；
//   2. 值改回初始 → 不脏（这就是原 bug）；
//   3. 合成事件把「空」写成「空」→ 不脏（原 bug 2）；
//   4. 保存成功后不再拦人，且 reset() 能以当前值为新基准。

const SRC = fs.readFileSync(new URL('../../app/erp/static/order-entry-common.js', import.meta.url), 'utf8');

/** 用一个最小的假表单把 createDirtyTracker 跑起来。 */
function loadTracker(fields) {
  // 只取 createDirtyTracker 这一段，避免把依赖 window/document 的模块整体求值。
  const start = SRC.indexOf('function createDirtyTracker');
  assert.ok(start > 0, 'createDirtyTracker 必须存在于 order-entry-common.js');
  const end = SRC.indexOf('/* 往来对象（hidden `customer_id`）变化监听', start);
  assert.ok(end > start, '找不到 createDirtyTracker 的结束位置');
  const code = SRC.slice(start, end);

  const form = {
    querySelectorAll() {
      return fields.map(f => ({
        name: f.name,
        value: f.value,
        type: f.type || 'text',
        checked: !!f.checked,
      }));
    },
  };
  const context = vm.createContext({});
  vm.runInContext(code + '\nthis.__create = createDirtyTracker;', context);
  return { tracker: context.__create(form), form };
}

function makeFields(initial) {
  return Object.entries(initial).map(([name, value]) => ({ name, value }));
}

test('值变化后判为脏', () => {
  const fields = makeFields({ customer_name: '', quantity: '' });
  const { tracker } = loadTracker(fields);
  assert.equal(tracker.isDirty(), false, '刚打开不该脏');

  fields[0].value = '张三';
  assert.equal(tracker.isDirty(), true, '输入内容后必须脏');
});

test('内容删回初始状态后不再判脏（原 bug）', () => {
  const fields = makeFields({ customer_name: '', quantity: '' });
  const { tracker } = loadTracker(fields);

  fields[0].value = '张三';
  fields[1].value = '3';
  assert.equal(tracker.isDirty(), true);

  // 用户把内容全删干净 —— 页面回到空，就不该再拦人。
  fields[0].value = '';
  fields[1].value = '';
  assert.equal(tracker.isDirty(), false, '删空后不该再判脏');
});

test('合成 input 事件把空值写成空值，不算脏（单位「×」缺陷）', () => {
  const fields = makeFields({ unit: '' });
  const { tracker } = loadTracker(fields);

  // 单位候选的「×」清空按钮：input.value = '' 后再派发合成 input。
  // 值本来为空、写回还是空 —— 指纹不变，不能因此判脏。
  fields[0].value = '';
  assert.equal(tracker.isDirty(), false, '空值写空值不该判脏');
});

test('保存成功后不再拦人', () => {
  const fields = makeFields({ customer_name: '' });
  const { tracker } = loadTracker(fields);

  fields[0].value = '张三';
  assert.equal(tracker.isDirty(), true);

  tracker.markSaved();
  assert.equal(tracker.isDirty(), false, '已保存的单据离开时不该提醒');
});

test('reset 以当前值为新基准', () => {
  const fields = makeFields({ customer_name: '' });
  const { tracker } = loadTracker(fields);

  fields[0].value = '张三';
  tracker.reset();
  assert.equal(tracker.isDirty(), false, 'reset 后当前值即基准');

  fields[0].value = '李四';
  assert.equal(tracker.isDirty(), true, 'reset 之后再有改动仍要判脏');
});

test('动态增删行体现在指纹里（加行算脏、删回原样不算）', () => {
  // 初始只有一行；加一行后字段变多，删回后又与初始一致。
  const fields = makeFields({ product_name: '', quantity: '' });
  const { tracker } = loadTracker(fields);

  fields.push({ name: 'product_name', value: '' }, { name: 'quantity', value: '' });
  assert.equal(tracker.isDirty(), true, '新增一行应判脏');

  fields.splice(2, 2);
  assert.equal(tracker.isDirty(), false, '删回一行后不该再判脏');
});

test('不带 name 的控件不参与指纹（按钮/提示行）', () => {
  const fields = makeFields({ customer_name: '' });
  const { tracker } = loadTracker(fields);

  // 换页提示行、行内按钮都不带 name —— 它们出现不该被判脏。
  fields.push({ name: '', value: '本张已满 10 行' });
  assert.equal(tracker.isDirty(), false, '无名控件不该影响判脏');
});
