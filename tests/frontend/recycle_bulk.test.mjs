import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// 回归背景：回收站「恢复选中 / 确认删除选中」曾经完全没反应——商品/客户卡片的勾选框叫
// product_ids/customer_ids，而按钮走全局 submitSelected()（只认 name="ids"），form 永远为空。
// 该缺陷在 576 项 pytest 里完全没被覆盖，因为没有任何测试真正执行过这段 JS。
// 本文件在 Node 里真跑模板里的那段脚本，断言它对各卡片的真实提交行为。

const source = fs.readFileSync(new URL('../../app/erp/templates/recycle/index.html', import.meta.url), 'utf8');
const start = source.indexOf('<script>');
const end = source.indexOf('</script>', start);
assert(start >= 0 && end > start, 'recycle/index.html must keep an inline <script> block');
const script = source.slice(start + '<script>'.length, end);

// --- 极简 DOM 桩：只实现模板脚本用到的那几个选择器形态 ---
const ATTR_SELECTOR = /^([a-zA-Z]*)((?:\[[^\]]+\])*)(:checked)?$/;

function parseSelector(selector) {
  const match = ATTR_SELECTOR.exec(selector.trim());
  if (!match) throw new Error('unsupported selector in stub: ' + selector);
  const [, tag, attrPart, checked] = match;
  const attrs = [...attrPart.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)].map(m => ({ key: m[1], value: m[2] }));
  return { tag, attrs, checked: Boolean(checked) };
}

class El {
  constructor(tag, attrs = {}, options = {}) {
    this.tag = tag;
    this.attrs = { ...attrs };
    this.children = [];
    this.parent = null;
    this.checked = Boolean(options.checked);
    this.hidden = Boolean(options.hidden);
    this.value = attrs.value ?? '';
    this.name = attrs.name ?? '';
    this.type = attrs.type ?? '';
    this.submitted = false;
    this.listeners = new Map();
    this.dataset = {};
    for (const [key, value] of Object.entries(attrs)) {
      if (key.startsWith('data-')) {
        this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
      }
    }
  }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  set innerHTML(_) { this.children = []; }
  get innerHTML() { return ''; }
  submit() { this.submitted = true; }
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }
  dispatch(type, event = {}) {
    for (const handler of this.listeners.get(type) || []) handler({ target: this, ...event });
  }
  click() { this.dispatch('click'); }
  matches({ tag, attrs, checked }) {
    if (tag && this.tag !== tag) return false;
    if (checked && !this.checked) return false;
    return attrs.every(({ key, value }) => value === undefined ? this.attrs[key] !== undefined : this.attrs[key] === value);
  }
  *walk() {
    for (const child of this.children) { yield child; yield* child.walk(); }
  }
  querySelectorAll(selector) {
    const parsed = parseSelector(selector);
    return [...this.walk()].filter(node => node.matches(parsed));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(tag) {
    for (let node = this; node; node = node.parent) if (node.tag === tag) return node;
    return null;
  }
}

// --- 按模板真实标记搭出三张卡片 ---
function checkboxCard({ formId, checkboxName, section, submitMessage }) {
  const card = new El('section', { 'data-recycle-section': '', 'data-recycle-checkbox': checkboxName, 'data-recycle-form': formId });
  const headAll = new El('input', { type: 'checkbox', 'data-recycle-check-all': '1' });
  card.appendChild(headAll);
  const table = new El('table');
  card.appendChild(table);
  const rows = [];
  for (let index = 1; index <= 2; index += 1) {
    const tr = new El('tr');
    const box = new El('input', { type: 'checkbox', name: checkboxName, value: String(index) });
    tr.appendChild(box);
    table.appendChild(tr);
    rows.push(box);
  }
  card.appendChild(new El('button', { type: 'button', 'data-recycle-submit': 'restore', 'data-recycle-message': submitMessage }));
  card.appendChild(new El('button', { type: 'button', 'data-recycle-submit': 'purge', 'data-recycle-message': submitMessage }));
  const form = new El('form', { id: formId, method: 'post', action: `/recycle/bulk/${section}/${'purge'}` });
  return { card, headAll, rows, form };
}

function buildDocument() {
  const typeBoxes = ['sale', 'return'].map(value => new El('input', { type: 'checkbox', name: 'recycle_type', value }));
  const docRows = [
    new El('tr', { 'data-recycle-kind': 'sale', 'data-document-key': 'sale:1' }),
    new El('tr', { 'data-recycle-kind': 'purchase_return', 'data-document-key': 'purchase_return:9' }),
  ];
  const docCard = new El('section', { 'data-recycle-section': '', 'data-recycle-checkbox': 'recycle_ids', 'data-recycle-form': 'recycleBulkForm' });
  const docHeadAll = new El('input', { type: 'checkbox', 'data-recycle-check-all': '1' });
  docCard.appendChild(docHeadAll);
  const docBoxes = [];
  docRows.forEach((row, index) => {
    const box = new El('input', {
      type: 'checkbox', name: 'recycle_ids', value: row.attrs['data-document-key'],
      'data-recycle-version': index === 1 ? '7' : '',
    });
    row.appendChild(box);
    docCard.appendChild(row);
    docBoxes.push(box);
  });
  docCard.appendChild(new El('button', { type: 'button', 'data-recycle-submit': 'restore', 'data-recycle-message': '确认恢复选中的单据吗？' }));
  docCard.appendChild(new El('button', { type: 'button', 'data-recycle-submit': 'purge', 'data-recycle-message': '确认彻底删除选中的单据吗？' }));
  const docForm = new El('form', { id: 'recycleBulkForm', method: 'post', action: '/recycle/bulk' });

  const products = checkboxCard({ formId: 'recycleProductForm', checkboxName: 'product_ids', section: 'product', submitMessage: '确认恢复选中的商品吗？' });
  const customers = checkboxCard({ formId: 'recycleCustomerForm', checkboxName: 'customer_ids', section: 'customer', submitMessage: '确认恢复选中的客户吗？' });

  const countLabel = new El('span', { id: 'recycleTypeCount' });
  const typeAll = new El('button', { type: 'button', 'data-recycle-type-all': '1' });
  const typeNone = new El('button', { type: 'button', 'data-recycle-type-none': '1' });

  const body = new El('div');
  [docCard, products.card, customers.card, countLabel, typeAll, typeNone, ...typeBoxes, docForm, products.form, customers.form]
    .forEach(node => body.appendChild(node));
  const created = [];
  const document = {
    body,
    querySelector: selector => body.querySelector(selector),
    querySelectorAll: selector => body.querySelectorAll(selector),
    getElementById: id => body.querySelectorAll(`[id="${id}"]`)[0] || null,
    createElement: tag => { const node = new El(tag); created.push(node); return node; },
  };
  return { document, docCard, docHeadAll, docBoxes, docForm, products, customers, typeBoxes, docRows, countLabel, created };
}

function run({ confirmAnswer = true, alert } = {}) {
  const dom = buildDocument();
  const alerts = [];
  const context = vm.createContext({
    document: dom.document,
    alert: alert || (message => alerts.push(message)),
    confirm: () => confirmAnswer,
  });
  vm.runInContext(script, context);
  return { ...dom, alerts };
}

const actionsOf = form => form.children.filter(child => child.name === 'ids').map(child => child.value);
const actionField = form => form.children.find(child => child.name === 'action')?.value;

test('商品卡片「恢复选中」提交到按类型 restore 端点并带上勾选 id', () => {
  const { products, alerts } = run();
  products.rows[0].checked = true;
  products.rows[1].checked = true;
  products.card.querySelectorAll('[data-recycle-submit]')[0].click();

  assert.deepEqual(alerts, [], '不应该提示未选中');
  assert.equal(products.form.action, '/recycle/bulk/product/restore');
  assert.deepEqual(actionsOf(products.form), ['1', '2']);
  assert.equal(products.form.submitted, true);
});

test('商品卡片「确认删除选中」提交到按类型 purge 端点', () => {
  const { products } = run();
  products.rows[1].checked = true;
  products.card.querySelectorAll('[data-recycle-submit]')[1].click();

  assert.equal(products.form.action, '/recycle/bulk/product/purge');
  assert.deepEqual(actionsOf(products.form), ['2']);
  assert.equal(products.form.submitted, true);
});

test('客户卡片同样能批量提交（旧版 submitSelected 只认 name="ids"，这里必然为空）', () => {
  const { customers, alerts } = run();
  customers.rows[0].checked = true;
  customers.card.querySelectorAll('[data-recycle-submit]')[0].click();

  assert.deepEqual(alerts, []);
  assert.equal(customers.form.action, '/recycle/bulk/customer/restore');
  assert.deepEqual(actionsOf(customers.form), ['1']);
  assert.equal(customers.form.submitted, true);
});

test('未勾选时提示且不提交', () => {
  const { products, alerts } = run();
  products.card.querySelectorAll('[data-recycle-submit]')[0].click();

  assert.deepEqual(alerts, ['请先选中要操作的记录']);
  assert.equal(products.form.submitted, false);
  assert.deepEqual(actionsOf(products.form), []);
});

test('confirm 取消后不提交', () => {
  const { products } = run({ confirmAnswer: false });
  products.rows[0].checked = true;
  products.card.querySelectorAll('[data-recycle-submit]')[1].click();

  assert.equal(products.form.submitted, false);
  assert.deepEqual(actionsOf(products.form), []);
});

test('单据卡片走合并端点，并注入 action 字段与退拿货版本号', () => {
  const { docBoxes, docForm, docCard } = run();
  docBoxes[0].checked = true;
  docBoxes[1].checked = true;
  docCard.querySelectorAll('[data-recycle-submit]')[0].click();

  assert.equal(docForm.action, '/recycle/bulk');
  assert.equal(actionField(docForm), 'restore');
  assert.deepEqual(actionsOf(docForm), ['sale:1', 'purchase_return:9']);
  const versioned = docForm.children.filter(child => child.name.startsWith('version_'));
  assert.deepEqual(versioned.map(child => [child.name, child.value]), [['version_9', '7']]);
  assert.equal(docForm.submitted, true);
});

test('卡片全选只作用于本卡片，不会串到另一张卡片', () => {
  const { products, customers } = run();
  products.headAll.checked = true;
  products.headAll.dispatch('change', { target: products.headAll });

  assert.deepEqual(products.rows.map(row => row.checked), [true, true]);
  assert.deepEqual(customers.rows.map(row => row.checked), [false, false]);
});

test('重复点击不会累积隐藏字段（每次提交重建 form 内容）', () => {
  const { products } = run();
  products.rows[0].checked = true;
  products.card.querySelectorAll('[data-recycle-submit]')[0].click();
  products.card.querySelectorAll('[data-recycle-submit]')[1].click();

  assert.deepEqual(actionsOf(products.form), ['1'], '第二次点击不应留下上一次的 ids');
  assert.equal(products.form.action, '/recycle/bulk/product/purge');
});
