// rank_size 是服务端接受的展示条件（每组显示条数），必须允许写入。
// v15（2026-10-16）：展示条件白名单已收窄，去掉随本次下线一起删除的视图开关。
const VIEW_KEYS = new Set(['tab','rank_key','rank_size','rank_unit','rank_page','rank_full','sort','metric','day']);
// 结果区块标记与就地刷新实现已抽到共享模块（app/erp/static/page-refresh.mjs），
// 避免「同一件事两套实现」漂移。此处只 re-export，既有调用方与单测不受影响。
import {REFRESH_FRAGMENT, refreshInPlace, scrollAnchor} from './page-refresh.mjs';
export {REFRESH_FRAGMENT, refreshInPlace, scrollAnchor};
// 条形统计图生长速度：恒定 px/秒（不是恒定总时长）。最高柱 190px ≈ 0.79s。
export const BAR_GROW_SPEED_PX_S = 240;

/**
 * 每根柱的生长时长 = 柱高 ÷ 恒定速度（秒）。
 * 速度恒定 ⇒ 矮柱先到顶、高柱用时更长；这是本动画的核心口径，不要改成「所有柱同时长完」。
 */
export function growDurations(heights, speed = BAR_GROW_SPEED_PX_S) {
  return heights.map(height => Math.max(0, Number(height) || 0) / speed);
}

/**
 * 一组柱图的生长计划：按各自柱高定时长（速度恒定）。
 * 返回 seconds=0 表示这组不该播（例如全是零高）。
 */
export function growthPlan({heights = []} = {}) {
  const perBar = growDurations(heights);
  return {seconds: perBar.length ? Math.max(...perBar) : 0, perBar};
}

/**
 * 给一个图表容器写入生长所需的 CSS 变量并加上 .is-growing 触发动画。
 * 返回是否真的触发了动画（hidden 容器、零时长、减少动态效果都不触发）。
 */
export function applyBarGrowth(chart, {reducedMotion = false} = {}) {
  if (!chart || reducedMotion) return false;
  const fills = [...chart.querySelectorAll('.rank-bar-fill')];
  const heights = fills.map(fill => parseFloat(fill.style.height) || 0);
  const {seconds, perBar} = growthPlan({heights});
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

export function initAnalytics(doc) {
  const root=doc.querySelector('.erp-analytics');
  if (!root) return;
  root.dataset.scopeUrl = new URL(root.dataset.scopeUrl,location.href).href;
  const persist=updates=>{
    root.dataset.scopeUrl=withViewState(root.dataset.scopeUrl,updates);
    history.replaceState(null,'',root.dataset.scopeUrl + location.hash);
  };
  const tabs=()=>[...root.querySelectorAll('[data-tab]')];
  // 条形统计图竖向生长：只在「点页签切到产品排行」与「就地刷新排行区块」时触发。
  // 触发点放在 JS 而非纯 CSS，是为了让「就地刷新后整块替换」的新节点不带 .is-growing，天然不重播。
  const prefersReducedMotion = () =>
    typeof doc.defaultView?.matchMedia === 'function' && doc.defaultView.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const growBars = panel => {
    if (!panel) return;
    for (const chart of panel.querySelectorAll('.rank-columns')) {
      if (chart.closest('[hidden]')) continue;
      applyBarGrowth(chart, {reducedMotion: prefersReducedMotion()});
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
    // 只有真正切到产品排行才生长；点已选中的页签不重播。
    if (!wasSelected && key === 'rank') {
      growBars(doc.getElementById('rankPanel'));
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
  // 排行 / 每组显示：就地替换分析区块，保留阅读位置，不再整页跳回顶部。
  // 监听挂在 root 上（区块会被替换），因此必须用事件委托。
  const refreshSelects = new Set(['rank_key','rank_size']);
  let refreshing = false;
  // 当前选中的页签；只有产品排行里有需要生长的柱图。
  const activeTabKey = () => tabs().find(tab => tab.getAttribute('aria-selected') === 'true')?.dataset.tab;
  const growActivePanel = () => {
    if (activeTabKey() === 'rank') growBars(doc.getElementById('rankPanel'));
  };
  const applyViewChange = async updates => {
    if (refreshing) return;
    const url = withViewState(root.dataset.scopeUrl,updates);
    refreshing = true;
    try {
      await refreshInPlace(root, url);
      root.dataset.scopeUrl = url;
      history.replaceState(null,'',url + location.hash);
      // 图表数据被就地换掉了（换榜 / 换每组显示 / 点显示）→ 重播生长。
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
  root.addEventListener('change',event=>{
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
}

if (typeof document !== 'undefined') initAnalytics(document);
