import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
const moduleUrl = new URL('../../app/erp/static/analytics-controls.mjs', import.meta.url);

test('view changes preserve the applied scope and blank date boundaries', async () => {
  assert(existsSync(moduleUrl), 'formal analytics interaction controller is missing');
  const {withViewState} = await import(moduleUrl);
  const before = 'http://127.0.0.1:5000/analytics/?start_date=&end_date=&customer_id=7&product_name=%E6%B6%88%E9%98%B2%26A&spec=__unfilled__&metric=amount&tab=rank&rank_page=2';
  const after = new URL(withViewState(before, {metric:'quantity', sort:'profit', rank_page:null, tab:'health'}));
  for (const key of ['start_date','end_date','customer_id','product_name','spec']) {
    assert.equal(after.searchParams.get(key), new URL(before).searchParams.get(key));
  }
  assert.equal(after.searchParams.get('metric'),'quantity');
  assert.equal(after.searchParams.get('tab'),'health');
  assert.equal(after.searchParams.has('rank_page'),false);
  assert.throws(() => withViewState(before,{customer_id:9}), /展示/);
});

test('changing product rebuilds only its models and gives the blank model its own value', async () => {
  assert(existsSync(moduleUrl), 'formal analytics interaction controller is missing');
  const {specOptions} = await import(moduleUrl);
  const products=[{name:'部件',spec:''},{name:'部件',spec:'Q1'},{name:'水带',spec:'20m'}];
  assert.deepEqual(specOptions(products,'部件'),[
    {value:'',label:'全部型号'}, {value:'__unfilled__',label:'未填写型号'}, {value:'Q1',label:'Q1'},
  ]);
  assert.deepEqual(specOptions(products,''),[{value:'',label:'全部型号'}]);
});

test('rank_size is an allowed view key so 每组显示 can refresh without reloading', async () => {
  const {withViewState} = await import(moduleUrl);
  const base = 'http://127.0.0.1:5000/analytics/?tab=rank&rank_key=hot_sales&rank_size=10';
  const after = new URL(withViewState(base, {rank_key:'low_sales', rank_size:'50'}));
  assert.equal(after.searchParams.get('rank_key'), 'low_sales');
  assert.equal(after.searchParams.get('rank_size'), '50');
});

test('in-place refresh replaces only the result fragments and restores the scroll anchor', async () => {
  const {refreshInPlace} = await import(moduleUrl);
  // Minimal DOM/DOMParser stand-ins: we assert the replacement contract, not rendering.
  const makeEl = (id, tag='section') => {
    const el = {
      id, tagName: tag.toUpperCase(), hidden: false, children: [],
      replaceWith(node) { this.replacedWith = node; },
    };
    return el;
  };
  const currentRank = makeEl('rankPanel');
  const currentMetrics = makeEl('analysisMetrics');
  const doc = {
    querySelector: () => null,
    getElementById: id => ({rankPanel: currentRank, analysisMetrics: currentMetrics}[id] || null),
    importNode: node => node,
    scrollingElement: {scrollTop: 0},
  };
  const freshRank = makeEl('rankPanel');
  const freshMetrics = makeEl('analysisMetrics');
  const freshPage = {
    querySelectorAll: () => [
      Object.assign(freshRank, {getAttribute: () => 'data-analytics-fragment'}),
      Object.assign(freshMetrics, {getAttribute: () => 'data-analytics-fragment'}),
    ],
  };
  const root = {ownerDocument: doc, querySelectorAll: () => [], addEventListener() {}};
  const fetchImpl = async () => ({ok: true, text: async () => '<html></html>'});
  const originalParser = globalThis.DOMParser;
  globalThis.DOMParser = class { parseFromString() { return freshPage; } };
  try {
    const replaced = await refreshInPlace(root, 'http://127.0.0.1:5000/analytics/?tab=rank', doc, fetchImpl);
    assert.equal(replaced, 2, '两个结果区块都应被替换');
    assert.equal(currentRank.replacedWith, freshRank);
    assert.equal(currentMetrics.replacedWith, freshMetrics);
  } finally {
    globalThis.DOMParser = originalParser;
  }
});

test('a failed refresh reports instead of silently leaving a stale chart', async () => {
  const {refreshInPlace} = await import(moduleUrl);
  const root = {ownerDocument: {querySelector: () => null, getElementById: () => null}, querySelectorAll: () => []};
  await assert.rejects(
    refreshInPlace(root, 'http://127.0.0.1:5000/analytics/', root.ownerDocument, async () => ({ok: false, status: 500})),
    /刷新失败/,
  );
});
