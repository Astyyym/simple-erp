// rank_size 是服务端接受的展示条件（每组显示条数），必须允许写入。
const VIEW_KEYS = new Set(['tab','rank_key','rank_size','rank_unit','rank_page','rank_full','sort','metric','day']);
const REFRESH_FRAGMENT = 'data-analytics-fragment';

export function withViewState(source, updates) {
  const url = new URL(source, typeof location === 'undefined' ? 'http://127.0.0.1' : location.href);
  for (const [key,value] of Object.entries(updates)) {
    if (!VIEW_KEYS.has(key)) throw new Error('只允许修改展示条件');
    if (value === null || value === undefined) url.searchParams.delete(key);
    else url.searchParams.set(key,String(value));
  }
  return url.href;
}

/** Page offset that must survive an in-place refresh; prefers the scrolling pane. */
export function scrollAnchor(doc) {
  const pane = doc.querySelector('.page-body');
  if (pane && pane.scrollTop) return {target: pane, top: pane.scrollTop};
  const scroller = doc.scrollingElement || doc.documentElement;
  const top = typeof window === 'undefined' ? (scroller ? scroller.scrollTop : 0) : (window.scrollY || 0);
  return {target: scroller, top};
}

/**
 * Replace the analytics result fragments in place instead of reloading the page,
 * so choosing 排行 / 每组显示 no longer throws the reader back to the top.
 */
export async function refreshInPlace(root, url, doc = root.ownerDocument, fetchImpl = fetch) {
  const response = await fetchImpl(url, {headers: {'X-Requested-With': 'fetch'}});
  if (!response.ok) throw new Error('刷新失败');
  const fresh = new DOMParser().parseFromString(await response.text(), 'text/html');
  const anchor = scrollAnchor(doc);
  let replaced = 0;
  for (const section of fresh.querySelectorAll('[' + REFRESH_FRAGMENT + ']')) {
    const id = section.id;
    if (!id) continue;
    const current = doc.getElementById(id);
    if (!current) continue;
    current.replaceWith(doc.importNode(section, true));
    replaced += 1;
  }
  if (!replaced) throw new Error('未找到可替换的分析区块');
  const anchorNode = doc.querySelector('.page-body') || doc.scrollingElement || doc.documentElement;
  if (anchor.target === anchorNode) anchorNode.scrollTop = anchor.top;
  else (doc.scrollingElement || doc.documentElement).scrollTop = anchor.top;
  return replaced;
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
  const selectTab=(key,save=true)=>{
    for (const tab of tabs()) {
      const selected=tab.dataset.tab === key;
      tab.setAttribute('aria-selected',String(selected));
      tab.tabIndex=selected ? 0 : -1;
      const panel = doc.getElementById(tab.getAttribute('aria-controls'));
      if (panel) panel.hidden=!selected;
    }
    root.querySelectorAll('[data-view-form] input[name=tab]').forEach(input=>input.value=key);
    if (save) persist({tab:key});
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
  const applyViewChange = async updates => {
    if (refreshing) return;
    const url = withViewState(root.dataset.scopeUrl,updates);
    refreshing = true;
    try {
      await refreshInPlace(root, url);
      root.dataset.scopeUrl = url;
      history.replaceState(null,'',url + location.hash);
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
