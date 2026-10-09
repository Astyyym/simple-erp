import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Batch 0（2026-10-09 旧数据迁移承接计划）：顶栏实时钟。
// 背景：工作台已有静态「统计日期」，但随页面滚动消失；顶栏需要一枚钉住可见的实时时间。
// 约定：YYYY-MM-DD HH:MM:SS、24 小时制、零填充、由 JS 每秒刷新（禁止服务端渲染，否则时间冻住）。
// 本文件真跑 base.html 里那段时钟 IIFE，而不是复述一份可能与实现漂移的逻辑。

const template = fs.readFileSync(new URL('../../app/erp/templates/base.html', import.meta.url), 'utf8');

// 结构断言：顶栏必须存在 #topbarClock 容器。
assert.match(
  template,
  /<div class="topbar-clock" id="topbarClock"[^>]*><\/div>/,
  'base.html 顶栏必须提供 #topbarClock 容器',
);

// 取出真正的时钟脚本块。
const match = template.match(/\/\/ Batch 0：顶栏实时钟[\s\S]*?\}\)\(\);/);
assert(match, 'base.html 必须保留顶栏实时钟脚本');
const clockScript = match[0];

assert.match(clockScript, /setInterval\(render,\s*1000\)/, '时钟必须每秒刷新，不能用一次性渲染');

function runClock(nowIso) {
  const element = { textContent: '' };
  const intervals = [];
  const context = vm.createContext({
    document: { getElementById: (id) => (id === 'topbarClock' ? element : null) },
    Date: class extends Date {
      constructor() {
        super(nowIso);
      }
    },
    setInterval: (fn, ms) => {
      intervals.push({ fn, ms });
      return intervals.length;
    },
  });
  vm.runInContext(clockScript, context);
  return { element, intervals };
}

test('首次渲染即给出 YYYY-MM-DD HH:MM:SS 零填充格式', () => {
  const { element, intervals } = runClock('2026-10-09T08:05:07');
  assert.equal(element.textContent, '2026-10-09 08:05:07');
  assert.equal(intervals.length, 1);
  assert.equal(intervals[0].ms, 1000);
});

test('24 小时制：下午不出现 12 小时制歧义', () => {
  const { element } = runClock('2026-10-09T21:59:59');
  assert.equal(element.textContent, '2026-10-09 21:59:59');
});

test('定时器回调会重新取值，秒数真的在走', () => {
  let current = new Date('2026-10-09T08:05:07');
  const element = { textContent: '' };
  let tick = null;
  const context = vm.createContext({
    document: { getElementById: (id) => (id === 'topbarClock' ? element : null) },
    Date: class extends Date {
      constructor() {
        super(current.getTime());
      }
    },
    setInterval: (fn) => {
      tick = fn;
      return 1;
    },
  });
  vm.runInContext(clockScript, context);
  assert.equal(element.textContent, '2026-10-09 08:05:07');
  current = new Date('2026-10-09T08:05:08');
  assert.equal(typeof tick, 'function', '必须注册定时器回调');
  tick();
  assert.equal(element.textContent, '2026-10-09 08:05:08');
});
