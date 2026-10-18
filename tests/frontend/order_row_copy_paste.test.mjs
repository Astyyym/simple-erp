/**
 * 开单行复制 / 粘贴（2026-10-17 用户需求）
 *
 * 场景：同种商品不同型号连续开单。一物一档（消防水带DN50 / DN65 是两条商品记录），
 * 复制一行 → 粘贴到下一行 → 只改型号 → 该型号在该客户那里有专属价就自动带出。
 *
 * 这个测试守的是**最危险的一条线**：粘贴过来的单价属于上一行那个型号，
 * 而现有规则是「手输单价 > 0 会被学成该客户对该商品的专属价」。
 * 如果改型号后旧价还留在行上，错价会被永久学进档案。所以：
 *   - 名称没变 → 保留粘贴的价（用户确认继续用）
 *   - 名称变了 → 必须先把价清空
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const COMMON_PATH = fileURLToPath(new URL('../../app/erp/static/order-entry-common.js', import.meta.url));
const COMMON_SRC = fs.readFileSync(COMMON_PATH, 'utf8');

const TEMPLATES = [
  'app/erp/templates/orders/new.html',
  'app/erp/templates/purchases/new.html',
  'app/erp/templates/purchases/return_new.html',
];

/** 造一个最小 DOM：tbody + 若干行，每行有 picker-name-input / unit / quantity / unit-price / product-id。 */
function makeRow({ name = '', unit = '', quantity = '', price = '' } = {}) {
  const values = {
    '.picker-name-input': name,
    '.picker-name-input-sync': name,
    '.unit': unit,
    '.quantity': quantity,
    '.unit-price': price,
    '.product-id': '',
    '.product-name': name,
  };
  const added = [];
  const classes = new Set();
  const row = {
    __values: values,
    querySelector(sel) {
      if (sel in values) {
        const el = {
          get value() { return values[sel]; },
          set value(v) { values[sel] = v; },
          select() { el.__selected = true; },
          focus() { el.__focused = true; },
        };
        return el;
      }
      if (sel === '.product-picker') return { appendChild(n) { added.push(n); }, querySelector: () => null };
      if (sel === '.row-tool-btn.is-copied') return null;
      return null;
    },
    querySelectorAll() { return []; },
    classList: {
      add(c) { classes.add(c); },
      remove(c) { classes.delete(c); },
      contains(c) { return classes.has(c); },
    },
    dataset: {},
    scrollIntoView() {},
  };
  return row;
}

function makeTbody(rows) {
  let listener = null;
  const tbody = {
    rows,
    __added: [],
    addRowHook: null,
    querySelectorAll(sel) {
      if (sel === 'tr:not(.slip-notice)') return tbody.rows;
      return [];
    },
    addEventListener(type, fn) { if (type === 'click') listener = fn; },
    __fireClick(btn) {
      listener({ preventDefault() {}, target: { closest: (s) => (s === '[data-row-action]' ? btn : rowOf(btn)) } });
    },
    __rowsOf: {},
  };
  function rowOf(btn) { return btn.__row; }
  return tbody;
}

/** 在 vm 里载入公共 JS，返回 ErpOrderEntry。 */
function loadCommon() {
  const sandbox = {
    window: {},
    document: {
      createElement: () => ({ className: '', textContent: '', setAttribute() {}, remove() {}, parentNode: null }),
      querySelectorAll: () => [],
    },
    setTimeout: () => 0,
    Set, Array, String, Object,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(COMMON_SRC, sandbox);
  return sandbox.window.ErpOrderEntry;
}

test('公共模块导出了复制/粘贴入口', () => {
  const api = loadCommon();
  assert.equal(typeof api.bindRowCopyTools, 'function');
  assert.equal(typeof api.dropPastedPriceOnRename, 'function');
  assert.equal(typeof api.resetCopiedRow, 'function');
  assert.equal(typeof api.hasCopiedRow, 'function');
});

test('dropPastedPriceOnRename：名称没变返回 false（价格保留）', () => {
  const api = loadCommon();
  const row = makeRow({ name: '消防水带DN50', price: '52.00' });
  row.__pastedFrom = '消防水带DN50';
  row.querySelector('.picker-name-input').value = '消防水带DN50';
  assert.equal(api.dropPastedPriceOnRename(row), false);
  assert.equal(row.__values['.unit-price'], '52.00', '名称没变不能清价');
});

test('dropPastedPriceOnRename：名称变了返回 true 且清空单价（防错价被学成专属价）', () => {
  const api = loadCommon();
  const row = makeRow({ name: '消防水带DN50', price: '52.00' });
  row.__pastedFrom = '消防水带DN50';
  row.querySelector('.picker-name-input').value = '消防水带DN65';
  assert.equal(api.dropPastedPriceOnRename(row), true);
  assert.equal(row.__values['.unit-price'], '', '换型号必须清价');
  assert.equal(row.__pastedFrom, null, '标记用过一次要清掉');
});

test('dropPastedPriceOnRename：不是粘贴来的行，永远不动价格', () => {
  const api = loadCommon();
  const row = makeRow({ name: '随便改', price: '99.00' });
  assert.equal(api.dropPastedPriceOnRename(row), false);
  assert.equal(row.__values['.unit-price'], '99.00');
});

test('三个开单模板都挂了复制/粘贴按钮组，且按钮在 picker-row 内部', () => {
  for (const file of TEMPLATES) {
    const html = fs.readFileSync(fileURLToPath(new URL(`../../${file}`, import.meta.url)), 'utf8');
    assert.match(html, /data-row-action="copy"/, `${file} 缺复制按钮`);
    assert.match(html, /data-row-action="paste"/, `${file} 缺粘贴按钮`);
    const toolIdx = html.indexOf('class="row-copy-tools"');
    const pickerIdx = html.indexOf('class="picker-row"');
    const btnIdx = html.indexOf('class="row-copy-tools"');
    assert.ok(pickerIdx >= 0 && toolIdx > pickerIdx, `${file} 按钮必须在 picker-row 内`);
    // 按钮必须贴着产品名称单元格，而不是跑到「操作」列。
    const clearIdx = html.indexOf('clearRow(this)');
    assert.ok(toolIdx < clearIdx, `${file} 按钮位置必须在清空按钮之前`);
  }
});

test('三个开单模板都绑定了 bindRowCopyTools（纯剪贴板，不再需要 addRow）', () => {
  for (const file of TEMPLATES) {
    const html = fs.readFileSync(fileURLToPath(new URL(`../../${file}`, import.meta.url)), 'utf8');
    const m = html.match(/bindRowCopyTools\(([\s\S]{0,240}?)\);/);
    assert.ok(m, `${file} 没有调用 bindRowCopyTools`);
    // 2026-10-18 用户决策：粘贴只写到当前行，不自动插到下一行、不自动建行，
    // 所以这里**不该**再传 addRow（传了说明又偷偷加了跳行逻辑）。
    assert.ok(!/addRow/.test(m[0]), `${file} 粘贴不该再依赖 addRow（改为纯剪贴板）`);
  }
});

test('粘贴只写当前行：不跳下一行、不新建行、不抢焦点', () => {
  const common = fs.readFileSync(COMMON_PATH, 'utf8');
  // 旧实现里有「取下一行 / 末尾建行 / focus+select」三段，现在都必须不存在。
  assert.ok(!/rows\[idx \+ 1\]/.test(common), '不该再取「下一行」作为粘贴目标');
  // 只检查 pasteRowInto 函数体内部（联想候选的 scrollIntoView 与本功能无关）。
  const fn = common.slice(common.indexOf('function pasteRowInto'), common.indexOf('function dropPastedPriceOnRename'));
  assert.ok(!/scrollIntoView/.test(fn), '粘贴不该自动滚动（说明还在建行）');
  assert.ok(!/\.select\(\)/.test(fn), '粘贴不该抢焦点并全选（用户要的是纯剪贴板）');
  assert.ok(!/addRow/.test(fn), '粘贴不该新建行');
  // pasteRowInto 的签名只剩 (tr, onAfterChange)
  assert.match(common, /function pasteRowInto\(tr, onAfterChange\)/);
});

test('三个开单模板都在改型号时清掉粘贴来的旧价', () => {
  for (const file of TEMPLATES) {
    const html = fs.readFileSync(fileURLToPath(new URL(`../../${file}`, import.meta.url)), 'utf8');
    assert.match(
      html,
      /__pastedFrom/,
      `${file} 必须记录粘贴来源名称（否则无法判断型号是否被改过）`,
    );
    assert.match(
      html,
      /dropPastedPriceIfRetyped|dropPastedPriceOnRename/,
      `${file} 必须调用清价逻辑`,
    );
  }
});

test('按钮宽度不新增表格列（icon 常显，不占操作列）', () => {
  const base = fs.readFileSync(
    fileURLToPath(new URL('../../app/erp/templates/base.html', import.meta.url)), 'utf8');
  assert.match(base, /\.row-copy-tools\{/, '缺 .row-copy-tools 样式');
  assert.match(base, /\.row-tool-btn\{/, '缺 .row-tool-btn 样式');
  assert.match(base, /\.row-tool-btn\.is-copied\{/, '缺「已复制」高亮态');
  // 操作列宽度不变：加按钮前是 80px（见 new.html 表头）。
  const newHtml = fs.readFileSync(
    fileURLToPath(new URL('../../app/erp/templates/orders/new.html', import.meta.url)), 'utf8');
  assert.match(newHtml, /<th style="width:80px">操作<\/th>/, '操作列宽度不该被改动');
});
