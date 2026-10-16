import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// 换页提示行（2026-10-13）：录入页第 10/20/30… 行后插不可编辑提示行。
// 断言位置、文案、幂等，以及「提示行不算数据行」——行号连续性依赖这一点。

const CASES = [
  { name: '销售/退货录入页', file: '../../app/erp/templates/orders/new.html', tbodyId: 'orderBody', dataRows: 'orderDataRows' },
  { name: '拿货录入页', file: '../../app/erp/templates/purchases/new.html', tbodyId: 'purchaseRows', dataRows: 'purchaseDataRows' },
  { name: '退拿货录入页', file: '../../app/erp/templates/purchases/return_new.html', tbodyId: 'returnRows', dataRows: 'returnDataRows' },
];

class Row {
  constructor() {
    this.className = '';
    this.children = [];
    this.parent = null;
  }
  get isNotice() { return String(this.className).split(/\s+/).includes('slip-notice'); }
  appendChild(node) { this.children.push(node); }
  after(node) {
    const index = this.parent.children.indexOf(this);
    this.parent.children.splice(index + 1, 0, node);
    node.parent = this.parent;
  }
  remove() {
    if (!this.parent) return;
    const index = this.parent.children.indexOf(this);
    if (index >= 0) this.parent.children.splice(index, 1);
    this.parent = null;
  }
}

class Tbody {
  constructor(rows) { this.children = rows; rows.forEach(row => { row.parent = this; }); }
  append(row) { this.children.push(row); row.parent = this; }
  querySelectorAll(selector) {
    const text = String(selector);
    if (text.includes(':not(.slip-notice)')) return this.children.filter(row => !row.isNotice);
    if (text.includes('tr.slip-notice')) return this.children.filter(row => row.isNotice);
    return [...this.children];
  }
}

function load(entry, count) {
  const source = fs.readFileSync(new URL(entry.file, import.meta.url), 'utf8');
  const start = source.indexOf('/* ---- 单联容量提示行');
  const end = source.indexOf('/* ---- 单联容量提示行 end ---- */', start);
  assert(start >= 0 && end > start, `${entry.name}: 缺少换页提示行代码块`);
  const block = source.slice(start, end);
  // v15（2026-10-16）：明细表删掉了成本列，列数随数据模型变化 —— 换页提示行必须横跨整行，
  // 所以期望列数从模板自己的明细表头（thead.table-light）推导，不再写死数字。
  const headMatch = source.match(/<thead class="table-light">([\s\S]*?)<\/thead>/);
  assert(headMatch, `${entry.name}: 找不到开单明细表头 thead.table-light`);
  const columnCount = (headMatch[1].match(/<th\b/g) || []).length;
  assert.match(block, /const slipSize = \{\{ slip_size \}\};/, `${entry.name}: 单联容量必须取自 jinja 注入值`);
  const code = block.replace(/const slipSize = .*?;/, 'const slipSize = 10;');
  const tbody = new Tbody(rows(count));
  const document = {
    createElement: () => new Row(),
    getElementById: id => (id === entry.tbodyId ? tbody : null),
  };
  const context = vm.createContext({ document });
  vm.runInContext(code, context);
  return { context, tbody, entry, columnCount };
}

function rows(count) {
  return Array.from({ length: count }, (_, index) => {
    const row = new Row();
    row.label = `#${index + 1}`;
    return row;
  });
}

for (const entry of CASES) {
  for (const [count, expected] of [[1, 0], [9, 0], [10, 0], [11, 1], [20, 1], [21, 2], [31, 3]]) {
    test(`${entry.name}: ${count} 行时出现 ${expected} 条换页提示`, () => {
      const { context, tbody, columnCount } = load(entry, count);

      context.refreshSlipNotices();

      const notices = tbody.children.filter(row => row.isNotice);
      assert.equal(notices.length, expected, `${count} 行应有 ${expected} 条提示`);

      // 位置：第 10/20… 条数据行之后；文案报出真实的下一张序号。
      notices.forEach((notice, index) => {
        const position = notice.parent.children.indexOf(notice);
        const expectedAfter = (index + 1) * 10;
        assert.equal(tbody.children.indexOf(notice), expectedAfter + index,
          `第 ${index + 1} 条提示应紧跟第 ${expectedAfter} 条数据行`);
        assert.equal(notice.parent.children[position - 1].isNotice, false);
        const cell = notice.children[0];
        assert.equal(cell.textContent, `本张已满 10 行；因行数限制，以下内容续打在第 ${index + 2} 张`);
        assert.equal(cell.children.length, 0, '提示行内不得有输入控件');
        assert.equal(cell.colSpan, columnCount, '提示行必须横跨整行（列数取自模板明细表头）');
      });

      // 幂等：重复调用不会累积提示行，也不吃掉数据行。
      context.refreshSlipNotices();
      assert.equal(tbody.children.filter(row => row.isNotice).length, expected);
      assert.equal(context[entry.dataRows](tbody).length, count,
        '数据行数必须不变（recalc 的行号连续性依赖它）');
    });
  }
}

test('提示行不参与表单提交：整块代码不产生任何 name 字段', () => {
  for (const entry of CASES) {
    const source = fs.readFileSync(new URL(entry.file, import.meta.url), 'utf8');
    const start = source.indexOf('/* ---- 单联容量提示行');
    const end = source.indexOf('/* ---- 单联容量提示行 end ---- */', start);
    const block = source.slice(start, end);
    assert.doesNotMatch(block, /name\s*=/, `${entry.name}: 提示行代码块不应写入任何 name 字段`);
    assert.doesNotMatch(block, /<input/, `${entry.name}: 提示行代码块不应创建输入控件`);
  }
});
