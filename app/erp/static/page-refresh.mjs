// 共享「就地刷新」控制器：把筛选提交 / 翻页 / 页内切换从整页 GET 改成
// fetch → 只替换结果区块 → pushState 同步 URL → 恢复滚动位置。
//
// 为什么需要它：应用外壳把滚动放在内层 `.page-body`，不是文档本身。
// 普通 `method="get"` 表单提交是一次完整导航，浏览器自带的 scrollRestoration
// 对内部滚动容器无能为力，于是页面闪烁并回到最上方（2026-10-10 用户反馈）。
//
// 契约：
// - 只有带 `data-refresh-fragment` 的元素会被替换，按 id 一一对应。
//   新页面多出的区块会被插入，旧页面消失的区块会被移除。
// - 失败一律回落到整页导航，绝不把用户留在半截页面上。
// - 被替换区块内的 `<script>` 不会重新执行（importNode/replaceWith 的固有限制），
//   所以需要初始化的脚本必须放在 fragment 之外。

export const REFRESH_FRAGMENT = 'data-refresh-fragment';

/** 需要跨刷新保留的滚动位置；优先取滚动面板 `.page-body`。 */
export function scrollAnchor(doc) {
  const pane = doc.querySelector('.page-body');
  if (pane && pane.scrollTop) return {target: pane, top: pane.scrollTop};
  const scroller = doc.scrollingElement || doc.documentElement;
  const top = typeof window === 'undefined' ? (scroller ? scroller.scrollTop : 0) : (window.scrollY || 0);
  return {target: scroller, top};
}

export function restoreAnchor(doc, anchor) {
  if (!anchor || !anchor.target) return;
  anchor.target.scrollTop = anchor.top;
}

/**
 * 用 url 的响应替换当前文档中的刷新区块。
 * 返回被替换的区块数；一个都没换到就抛错（说明标记或 id 对不上，属于接线错误）。
 */
export async function refreshInPlace(root, url, doc = root.ownerDocument, fetchImpl = fetch,
                                     fragmentAttr = REFRESH_FRAGMENT) {
  const response = await fetchImpl(url, {headers: {'X-Requested-With': 'fetch'}});
  if (!response.ok) throw new Error('刷新失败');
  const fresh = new DOMParser().parseFromString(await response.text(), 'text/html');
  const anchor = scrollAnchor(doc);
  // 同时接受通用标记与历史遗留的 data-analytics-fragment，避免迁移期两套标记并存时漏换。
  const selector = '[' + fragmentAttr + ']' + (fragmentAttr === REFRESH_FRAGMENT ? ', [data-analytics-fragment]' : '');
  const freshSections = [...fresh.querySelectorAll(selector)];
  if (!freshSections.length) throw new Error('未找到可替换的结果区块');
  let replaced = 0;
  const freshIds = new Set();
  for (const section of freshSections) {
    const id = section.id;
    if (!id) continue;
    freshIds.add(id);
    const current = doc.getElementById(id);
    const node = doc.importNode(section, true);
    if (current && typeof current.replaceWith === 'function') {
      current.replaceWith(node);
      replaced += 1;
    } else if (current && current.parentNode) {
      current.parentNode.replaceChild(node, current);
      replaced += 1;
    }
  }
  // 旧页面有、新页面没有的区块 → 移除（例如筛选后 chip 行整体消失）。
  const existing = doc.querySelectorAll ? [...doc.querySelectorAll(selector)] : [];
  for (const stale of existing) {
    if (stale.id && !freshIds.has(stale.id) && stale.parentNode) {
      stale.parentNode.removeChild(stale);
      replaced += 1;
    }
  }
  restoreAnchor(doc, anchor);
  return replaced;
}

/** 表单当前值 → 查询串，用于 pushState 后仍然可分享 / 可后退。 */
export function formUrl(form, doc = form.ownerDocument) {
  const params = new URLSearchParams(new FormData(form));
  // 空值不写进 URL，避免 ?customer=&start_date=&end_date= 这类噪声。
  for (const [key, value] of [...params.entries()]) {
    if (value === '') params.delete(key);
  }
  const action = form.getAttribute('action') || doc.location.pathname;
  const base = new URL(action, doc.location.href);
  base.search = params.toString();
  base.hash = '';
  return base.href;
}

/**
 * 把一组 GET 表单与链接接成就地刷新。
 * - form 提交 → fetch 同 URL → 换块 → pushState
 * - 带 `data-inplace` 的链接（翻页、档案筛选）→ 同样处理
 * - fetch 失败 → 回落到 location.assign，用户不会卡住
 * 成功时派发 `erp:refreshed` 事件，供页面重播动画或重建联动。
 */
export function wireInPlace(root, doc = root.ownerDocument, options = {}) {
  const {onRefresh, fragmentAttr = REFRESH_FRAGMENT} = options;
  let busy = false;

  const apply = async url => {
    if (busy) return false;
    busy = true;
    try {
      await refreshInPlace(root, url, doc, fetch, fragmentAttr);
      if (doc.defaultView && doc.defaultView.history) {
        doc.defaultView.history.pushState({erpInPlace: true}, '', url);
      }
      if (onRefresh) onRefresh(url);
      doc.dispatchEvent(new CustomEvent('erp:refreshed', {detail: {url}}));
      return true;
    } catch (_) {
      doc.defaultView.location.assign(url);
      return false;
    } finally {
      busy = false;
    }
  };

  doc.addEventListener('submit', event => {
    const form = event.target.closest('form[data-inplace]');
    if (!form || !root.contains(form) || (form.method || 'get').toLowerCase() !== 'get') return;
    event.preventDefault();
    apply(formUrl(form, doc));
  });

  doc.addEventListener('click', event => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button > 0) return;
    const link = event.target.closest('a[data-inplace]');
    if (!link || !root.contains(link)) return;
    const href = link.getAttribute('href');
    if (!href || href.startsWith('#')) return;
    event.preventDefault();
    apply(new URL(href, doc.location.href).href);
  });

  // 后退 / 前进：重新拉取该 URL 并换块，滚动位置交给浏览器按 pushState 记录恢复。
  if (doc.defaultView) {
    doc.defaultView.addEventListener('popstate', () => {
      refreshInPlace(root, doc.defaultView.location.href, doc, fetch, fragmentAttr)
        .then(() => { if (onRefresh) onRefresh(doc.defaultView.location.href); })
        .catch(() => doc.defaultView.location.reload());
    });
  }
  return apply;
}

/** 页面入口：自动接线（module 在解析后执行，此时 DOM 已就绪）。 */
export function initPageRefresh(doc = document, options = {}) {
  const root = doc.querySelector('.page-body') || doc.body;
  if (!root) return null;
  // 页面没有任何 data-inplace 控件时不必接线（避免给纯展示页挂无用的监听）。
  if (!doc.querySelector('[data-inplace]')) return null;
  return wireInPlace(root, doc, options);
}

if (typeof document !== 'undefined') {
  // 暴露给内联脚本使用（模板里按需 initPageRefresh(...)）。
  window.erpPageRefresh = {initPageRefresh, refreshInPlace, scrollAnchor, formUrl, REFRESH_FRAGMENT};
  // 自动接线：module 默认 defer，执行时 DOM 已解析完，可直接初始化。
  // 各页只需在筛选表单/翻页链接上写 `data-inplace`，无需额外脚本。
  const boot = () => initPageRefresh(document);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
}
