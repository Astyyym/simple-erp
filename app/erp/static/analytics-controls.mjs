// rank_size 是服务端接受的展示条件（每组显示条数），必须允许写入。
const VIEW_KEYS = new Set(['tab','rank_key','rank_size','rank_unit','rank_page','rank_full','sort','metric','day','cost_view']);
// 结果区块标记与就地刷新实现已抽到共享模块（app/erp/static/page-refresh.mjs），
// 避免「同一件事两套实现」漂移。此处只 re-export，既有调用方与单测不受影响。
import {REFRESH_FRAGMENT, refreshInPlace, scrollAnchor} from './page-refresh.mjs';
export {REFRESH_FRAGMENT, refreshInPlace, scrollAnchor};
// 条形统计图生长速度：恒定 px/秒（不是恒定总时长）。最高柱 190px ≈ 0.79s，与环形扫掠 0.8s 同节奏。
export const BAR_GROW_SPEED_PX_S = 240;
// 横向堆叠条按自身像素宽度标定速度，展开观感与竖向柱同速。
export const STACK_GROW_SPEED_PX_S = 700;

/**
 * 每根柱的生长时长 = 柱高 ÷ 恒定速度（秒）。
 * 速度恒定 ⇒ 矮柱先到顶、高柱用时更长；这是本动画的核心口径，不要改成「所有柱同时长完」。
 */
export function growDurations(heights, speed = BAR_GROW_SPEED_PX_S) {
  return heights.map(height => Math.max(0, Number(height) || 0) / speed);
}

/**
 * 一组图表的生长计划。竖向柱按各自柱高定时长（速度恒定）；横向堆叠条按容器像素宽度定时长。
 * 返回 seconds=0 表示这组不该播（例如全是零高）。
 */
export function growthPlan({heights = [], widthPx = 0, kind = 'bars'} = {}) {
  if (kind === 'stack') {
    return {seconds: Math.max(0, Number(widthPx) || 0) / STACK_GROW_SPEED_PX_S, perBar: []};
  }
  const perBar = growDurations(heights);
  return {seconds: perBar.length ? Math.max(...perBar) : 0, perBar};
}

/**
 * 给一个图表容器写入生长所需的 CSS 变量并加上 .is-growing 触发动画。
 * 返回是否真的触发了动画（hidden 容器、零时长、减少动态效果都不触发）。
 */
export function applyBarGrowth(chart, {widthPx = 0, kind = 'bars', reducedMotion = false} = {}) {
  if (!chart || reducedMotion) return false;
  const fills = [...chart.querySelectorAll('.rank-bar-fill')];
  const heights = fills.map(fill => parseFloat(fill.style.height) || 0);
  const {seconds, perBar} = growthPlan({heights, widthPx, kind});
  if (!seconds) return false;
  chart.style.setProperty('--bar-dur', seconds.toFixed(3) + 's');
  fills.forEach((fill, index) => {
    const duration = (perBar[index] ?? seconds).toFixed(3) + 's';
    fill.style.setProperty('--bar-rise', (heights[index] || 0) + 'px');
    fill.style.setProperty('--bar-dur', duration);
    const label = fill.parentElement?.querySelector?.('.rank-bar-value');
    if (label) {
      label.style.setProperty('--bar-rise', (heights[index] || 0) + 'px');
      label.style.setProperty('--bar-dur', duration);
    }
  });
  // 强制回流后再加类，确保动画从头播（元素可能刚被替换/刚从 hidden 变可见）。
  void chart.offsetWidth;
  chart.classList.add('is-growing');
  return true;
}

export function withViewState(source, updates) {
  const url = new URL(source, typeof location === 'undefined' ? 'http://127.0.0.1' : location.href);
  for (const [key,value] of Object.entries(updates)) {
    if (!VIEW_KEYS.has(key)) throw new Error('只允许修改展示条件');
    if (value === null || value === undefined) url.searchParams.delete(key);
    else url.searchParams.set(key,String(value));
  }
  return url.href;
}

export function specOptions(products, name) {
  const options=[{value:'',label:'全部型号'}];
  if (!name) return options;
  const seen=new Set();
  for (const product of products) {
    if (product.name !== name || seen.has(product.spec)) continue;
    seen.add(product.spec);
    options.push({value:product.spec || '__unfilled__',label:product.spec || '未填写型号'});
  }
  return options;
}

export function initAnalytics(doc) {
  const root=doc.querySelector('.erp-analytics');
  if (!root) return;
  root.dataset.scopeUrl = new URL(root.dataset.scopeUrl,location.href).href;
  const ranking=doc.getElementById('rankingMode');
  const persist=updates=>{
    root.dataset.scopeUrl=withViewState(root.dataset.scopeUrl,updates);
    history.replaceState(null,'',root.dataset.scopeUrl + location.hash);
  };
  const tabs=()=>[...root.querySelectorAll('[data-tab]')];
  // 条形统计图竖向生长：只在这两个动作里触发——点页签切到 排行/成本诊断、成本视图从环形切回堆叠条。
  // 触发点放在 JS 而非纯 CSS，是为了让「就地刷新后整块替换」的新节点不带 .is-growing，天然不重播。
  const prefersReducedMotion = () =>
    typeof doc.defaultView?.matchMedia === 'function' && doc.defaultView.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const growBars = panel => {
    if (!panel) return;
    for (const chart of panel.querySelectorAll('.rank-columns,.cost-stack')) {
      if (chart.closest('[hidden]')) continue;
      const kind = chart.classList.contains('cost-stack') ? 'stack' : 'bars';
      const widthPx = kind === 'stack' ? chart.getBoundingClientRect().width : 0;
      applyBarGrowth(chart, {widthPx, kind, reducedMotion: prefersReducedMotion()});
    }
  };
  const selectTab=(key,save=true)=>{
    const wasSelected = root.querySelector(`[data-tab="${key}"]`)?.getAttribute('aria-selected') === 'true';
    for (const tab of tabs()) {
      const selected=tab.dataset.tab === key;
      tab.setAttribute('aria-selected',String(selected));
      tab.tabIndex=selected ? 0 : -1;
      const panel = doc.getElementById(tab.getAttribute('aria-controls'));
      if (panel) panel.hidden=!selected;
    }
    root.querySelectorAll('[data-view-form] input[name=tab]').forEach(input=>input.value=key);
    if (save) persist({tab:key});
    // 只有真正切到 产品排行 / 成本诊断 才生长；点已选中的页签不重播。
    if (!wasSelected && (key === 'rank' || key === 'health')) {
      growBars(doc.getElementById(key === 'rank' ? 'rankPanel' : 'healthPanel'));
    }
  };
  // 事件委托：区块被就地刷新后，页签与下拉不需要重新绑定。
  root.addEventListener('click',event=>{
    const day = event.target.closest('.heat-day[data-day]');
    if (day && root.contains(day)) { openDay(day, event); return; }
    const tab = event.target.closest('[data-tab]');
    if (tab && root.contains(tab)) selectTab(tab.dataset.tab);
  });
  root.addEventListener('keydown',event=>{
    const tab = event.target.closest('[data-tab]');
    if (!tab) return;
    const list = tabs();
    let index=list.indexOf(tab);
    if (event.key === 'ArrowRight') index=(index+1)%list.length;
    else if (event.key === 'ArrowLeft') index=(index+list.length-1)%list.length;
    else if (event.key === 'Home') index=0;
    else if (event.key === 'End') index=list.length-1;
    else return;
    event.preventDefault();selectTab(list[index].dataset.tab);list[index].focus();
  });
  const openAlert=()=>{
    if (location.hash !== '#inventoryAlert' || !ranking) return;
    // 排行模式与每组条数都由服务端渲染，跳转一次即可定位到库存告急。
    location.assign(withViewState(root.dataset.scopeUrl,{tab:'rank',rank_key:'inventory_alert'}));
  };
  openAlert();window.addEventListener('hashchange',openAlert);
  // 排行 / 每组显示：就地替换分析区块，保留阅读位置，不再整页跳回顶部。
  // 监听挂在 root 上（区块会被替换），因此必须用事件委托。
  const refreshSelects = new Set(['rank_key','rank_size']);
  let refreshing = false;
  // 当前选中的页签；只有 产品排行 / 成本诊断 里有需要生长的柱图。
  const activeTabKey = () => tabs().find(tab => tab.getAttribute('aria-selected') === 'true')?.dataset.tab;
  const growActivePanel = () => {
    const key = activeTabKey();
    if (key === 'rank' || key === 'health') {
      growBars(doc.getElementById(key === 'rank' ? 'rankPanel' : 'healthPanel'));
    }
  };
  const applyViewChange = async updates => {
    if (refreshing) return;
    const url = withViewState(root.dataset.scopeUrl,updates);
    refreshing = true;
    try {
      await refreshInPlace(root, url);
      root.dataset.scopeUrl = url;
      history.replaceState(null,'',url + location.hash);
      // 图表数据被就地换掉了（换榜 / 换每组显示 / 点显示 / 成本视图切换）→ 重播生长。
      // 只有整页加载与整页筛选重查不播，符合「切换时才出现」。
      growActivePanel();
    } catch (_) {
      location.assign(url);
    } finally {
      refreshing = false;
    }
  };
  // 热力图日期：只就地更新日历选区与右侧当天明细，不再整页 GET 重渲染。
  // 用事件委托挂在 root 上，区块被替换后依然有效。
  const openDay = async (link, event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button > 0) return;
    event.preventDefault();
    const day = link.dataset.day;
    if (!day || refreshing) return;
    // 右侧当天明细与日历同屏（sticky），刷新只替换区块，不移动视口。
    await applyViewChange({tab:'heat', day});
  };
  const collectRefreshUpdates = form => {
    const updates = {};
    for (const select of form.querySelectorAll('select')) {
      if (refreshSelects.has(select.name)) updates[select.name] = select.value;
    }
    return updates;
  };
  // 环形悬停：命中层 `cost-ring-hit` 判定（永不移动），视觉弧 `.cost-ring-seg` 外扩，浮标显示该段数值。
  // 圆心恒显示总量、不参与悬停。命中层与视觉层分离可避免「段外移→光标落空→状态抖动」。
  const ringSegFor = (view, label) => [...view.querySelectorAll('.cost-ring-seg')].find(seg => seg.dataset.segLabel === label);
  const clearRingTips = view => {
    view.classList.remove('is-hover');
    delete view.dataset.hoverSeg;
    view.querySelectorAll('.cost-ring-tip.is-active').forEach(tip => tip.classList.remove('is-active'));
    view.querySelectorAll('.cost-ring-seg.is-pop').forEach(seg => seg.classList.remove('is-pop'));
  };
  const activateRingSeg = (view, label) => {
    if (view.dataset.hoverSeg === label) return;
    clearRingTips(view);
    const seg = ringSegFor(view, label);
    if (seg) seg.classList.add('is-pop');
    const tip = [...view.querySelectorAll('.cost-ring-tip')].find(tip => tip.dataset.segLabel === label);
    if (tip) tip.classList.add('is-active');
    view.dataset.hoverSeg = label;
    view.classList.add('is-hover');
  };
  root.addEventListener('mouseover',event=>{
    const hit = event.target.closest('.cost-ring-hit');
    if (!hit || !root.contains(hit)) return;
    const view = hit.closest('.cost-ring-view');
    if (view) activateRingSeg(view, hit.dataset.segLabel);
  });
  root.addEventListener('mouseout',event=>{
    const hit = event.target.closest('.cost-ring-hit');
    if (!hit || !root.contains(hit)) return;
    if (hit.contains(event.relatedTarget)) return;
    const view = hit.closest('.cost-ring-view');
    if (view) clearRingTips(view);
  });
  root.addEventListener('change',event=>{
    const costRadio = event.target.closest('input[name=cost_view_ui]');
    if (costRadio && root.contains(costRadio)) {
      // 成本图表切换：就地刷新分层分析区块，保留阅读位置；生长由 applyViewChange 统一重播。
      applyViewChange({tab:'health', cost_view: costRadio.value});
      return;
    }
    const select = event.target.closest('[data-view-form] select');
    if (!select || !root.contains(select)) return;
    if (refreshSelects.has(select.name)) {
      // 「排行」和「每组显示」同属一张榜：改任一项都带上表单里的另一项。
      applyViewChange(collectRefreshUpdates(select.form));
      return;
    }
    const updates={[select.name]:select.value};
    if (select.name === 'metric') {
      updates.tab='heat';updates.day=null;
    }
    location.assign(withViewState(root.dataset.scopeUrl,updates));
  });
  // 排行表单的「显示」按钮同样就地刷新（rank_key + rank_size 一起提交）
  root.addEventListener('submit',event=>{
    const form = event.target.closest('form[data-view-form]');
    if (!form || !form.querySelector('[name=rank_key]')) return;
    event.preventDefault();
    applyViewChange(collectRefreshUpdates(form));
  });
  const product=doc.getElementById('productFilter'), spec=doc.getElementById('specFilter');
  const data=doc.getElementById('analysisProductOptions');
  if (product && spec && data) {
    const products=JSON.parse(data.textContent);
    product.addEventListener('change',()=>{
      spec.replaceChildren(...specOptions(products,product.value).map(option=>{
        const element=doc.createElement('option');element.value=option.value;element.textContent=option.label;return element;
      }));
      spec.disabled=!product.value;
    });
  }
}

if (typeof document !== 'undefined') initAnalytics(document);
