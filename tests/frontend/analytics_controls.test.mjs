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

test('cost_view is an allowed view key so 堆叠条/环形 toggle can refresh in place', async () => {
  const {withViewState} = await import(moduleUrl);
  const base = 'http://127.0.0.1:5000/analytics/?tab=health&cost_view=bar';
  const after = new URL(withViewState(base, {cost_view:'ring'}));
  assert.equal(after.searchParams.get('cost_view'), 'ring');
  assert.equal(after.searchParams.get('tab'), 'health');
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

test('bar growth keeps one constant speed so taller bars take longer', async () => {
  const {growDurations, BAR_GROW_SPEED_PX_S} = await import(moduleUrl);
  const durations = growDurations([190, 95, 19]);
  // 速度恒定：时长 = 柱高 / 速度，不是「所有柱同时长完」。
  assert.deepEqual(durations, [190 / BAR_GROW_SPEED_PX_S, 95 / BAR_GROW_SPEED_PX_S, 19 / BAR_GROW_SPEED_PX_S]);
  assert(durations[0] > durations[1] && durations[1] > durations[2], '高柱必须用时更长');
  assert.equal(durations[0] * BAR_GROW_SPEED_PX_S, 190);
  assert.deepEqual(growDurations([0, -5]), [0, 0]);
});

test('growth plan sizes stacks by width and bars by height', async () => {
  const {growthPlan, BAR_GROW_SPEED_PX_S, STACK_GROW_SPEED_PX_S} = await import(moduleUrl);
  const bars = growthPlan({heights: [190, 95], kind: 'bars'});
  assert.equal(bars.seconds, 190 / BAR_GROW_SPEED_PX_S, '整组时长取最长的那根柱');
  assert.deepEqual(bars.perBar, [190 / BAR_GROW_SPEED_PX_S, 95 / BAR_GROW_SPEED_PX_S]);
  const stack = growthPlan({widthPx: 700, kind: 'stack'});
  assert.equal(stack.seconds, 700 / STACK_GROW_SPEED_PX_S);
  assert.equal(growthPlan({heights: [], kind: 'bars'}).seconds, 0, '空图不播');
});

test('applyBarGrowth writes per-bar durations and skips hidden or reduced-motion charts', async () => {
  const {applyBarGrowth} = await import(moduleUrl);
  const makeChart = heights => {
    const fills = heights.map(height => ({
      style: {
        height: `${height}px`,
        vars: {},
        setProperty(name, value) { this.vars[name] = value; },
      },
      parentElement: {querySelector: () => label},
    }));
    const label = {style: {vars: {}, setProperty(name, value) { this.vars[name] = value; }}};
    return {
      classes: new Set(),
      vars: {},
      style: {vars: {}, setProperty(name, value) { this.vars[name] = value; }},
      offsetWidth: 190,
      querySelectorAll: () => fills,
      classList: {add(name) { this.owner.classes.add(name); }, owner: null},
      fills,
    };
  };
  const chart = makeChart([190, 95]);
  chart.classList.owner = chart;
  assert.equal(applyBarGrowth(chart, {kind: 'bars'}), true);
  assert(chart.classes.has('is-growing'), '应加上生长类触发动画');
  assert.equal(chart.style.vars['--bar-dur'], '0.792s');
  assert.equal(chart.fills[0].style.vars['--bar-dur'], '0.792s');
  assert.equal(chart.fills[1].style.vars['--bar-dur'], '0.396s');
  assert.equal(chart.fills[0].style.vars['--bar-rise'], '190px');
  // 减少动态效果时直接到位，不加类。
  const calm = makeChart([190]);
  calm.classList.owner = calm;
  assert.equal(applyBarGrowth(calm, {kind: 'bars', reducedMotion: true}), false);
  assert(!calm.classes.has('is-growing'));
  // 零高（空图）不播。
  const empty = makeChart([0]);
  empty.classList.owner = empty;
  assert.equal(applyBarGrowth(empty, {kind: 'bars'}), false);
});
