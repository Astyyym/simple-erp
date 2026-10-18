/* ============================================================================
   简单ERP 手机只读端 · 页面脚本
   不引用、不修改桌面端的 page-refresh.mjs / order-entry-common.js。

   只做五件事：
   1) 配对页 6 位码输入（自动前移、退格回退、粘贴分发）
   2) 导航等待：150ms 后出骨架，5 秒未返回换成明确失败（不无限转圈）
   3) 日期 sheet（年/月/日/全部 + 四个快捷）
   4) 下拉刷新（只在页面顶部接管手势，桌面端不注册）
   5) 外观三选（只存本机 localStorage，手机端没有任何写端）+ 闲置回首页
   ============================================================================ */
(function () {
  'use strict';

  var THEME_KEY = 'erp-mobile-theme';

  /* ---------------- 1. 配对页 6 位码 ---------------- */
  var digitInputs = Array.prototype.slice.call(document.querySelectorAll('.pair-code input'));
  var pairCodeField = document.getElementById('pairCode');
  if (digitInputs.length && pairCodeField) {
    var form = pairCodeField.form;

    var collect = function () {
      return digitInputs.map(function (input) { return input.value || ''; }).join('');
    };

    digitInputs.forEach(function (input, index) {
      input.addEventListener('input', function () {
        input.value = (input.value || '').replace(/\D/g, '').slice(-1);
        if (input.value && index < digitInputs.length - 1) {
          digitInputs[index + 1].focus();
          digitInputs[index + 1].select();
        }
      });
      input.addEventListener('keydown', function (event) {
        if (event.key === 'Backspace' && !input.value && index > 0) {
          digitInputs[index - 1].focus();
          digitInputs[index - 1].value = '';
          event.preventDefault();
        }
      });
      input.addEventListener('paste', function (event) {
        var text = (event.clipboardData || window.clipboardData).getData('text') || '';
        var digits = text.replace(/\D/g, '');
        if (!digits) { return; }
        event.preventDefault();
        for (var offset = 0; offset < digits.length && index + offset < digitInputs.length; offset += 1) {
          digitInputs[index + offset].value = digits.charAt(offset);
        }
        var next = Math.min(index + digits.length, digitInputs.length - 1);
        digitInputs[next].focus();
      });
    });

    if (form) {
      form.addEventListener('submit', function () {
        pairCodeField.value = collect();
      });
      if (digitInputs[0]) { digitInputs[0].focus(); }
    }
  }

  /* ---------------- 2. 导航等待：骨架 / 超时降级 ---------------- */
  var layer = document.getElementById('navLayer');
  var skeleton = document.getElementById('navSkeleton');
  var offline = document.getElementById('navOffline');
  var retryButton = document.getElementById('navRetry');
  var addressLine = document.getElementById('navAddress');
  var showTimer = null;
  var failTimer = null;
  var pendingUrl = location.href;
  var leaving = false;

  function clearTimers() {
    if (showTimer) { clearTimeout(showTimer); showTimer = null; }
    if (failTimer) { clearTimeout(failTimer); failTimer = null; }
  }

  function showOffline() {
    if (!layer || !skeleton || !offline) { return; }
    skeleton.hidden = true;
    offline.hidden = false;
    if (addressLine) { addressLine.textContent = location.host; }
  }

  // 局域网正常请求常 100ms 内返回：立刻挂骨架会闪一下再消失，比不显示更烦。
  function watchNavigation(url) {
    if (!layer || leaving) { return; }
    leaving = true;
    saveScroll(location.href);
    pendingUrl = url || location.href;
    clearTimers();
    showTimer = setTimeout(function () {
      layer.hidden = false;
      layer.setAttribute('aria-hidden', 'false');
      if (skeleton) { skeleton.hidden = false; }
      if (offline) { offline.hidden = true; }
      failTimer = setTimeout(showOffline, 5000);
    }, 150);
  }

  // 页面真的加载完成（served 了新文档）后，无论之前有没有降级到错误页，都要撤销遮罩。
  // 否则「超时先弹错误页 → 内容随后加载完」会让错误页一直盖着内容。
  function onDocumentSettled() {
    if (layer && !layer.hidden) {
      resetLayer();
    }
    restoreScroll(location.href);
  }

  function resetLayer() {
    clearTimers();
    leaving = false;
    if (layer) {
      layer.hidden = true;
      layer.setAttribute('aria-hidden', 'true');
    }
    // 复位两个子块：下次导航从「骨架可见 / 错误页隐藏」这个干净状态开始，
    // 否则上一次停在错误页，下一次连骨架都不显示就直接露错误页。
    if (skeleton) { skeleton.hidden = false; }
    if (offline) { offline.hidden = true; }
  }

  document.addEventListener('click', function (event) {
    var link = event.target && event.target.closest ? event.target.closest('a[href]') : null;
    if (!link) { return; }
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) { return; }
    if (link.target === '_blank' || link.hasAttribute('download')) { return; }
    var href = link.getAttribute('href') || '';
    if (!href || href.charAt(0) === '#' || href.indexOf('javascript:') === 0) { return; }
    watchNavigation(link.href);
  }, true);

  document.addEventListener('submit', function (event) {
    if (event.target && event.target.id === 'dateSheet') {
      // 日期 sheet 提交前先把当前选择写回隐藏字段。
      syncSheetFields();
    }
    watchNavigation();
  }, true);

  // pageshow 是 history 前进/后退**唯一**可靠的信号：那次导航不会重新执行页面脚本，
  // DOMContentLoaded 不会再触发，所以滚动恢复必须挂在这里，而不是只挂 DOMContentLoaded。
  window.addEventListener('pageshow', function (event) {
    resetLayer();
    restoreScroll(location.href);
  });
  // 本页脚本开始执行 = 旧文档已被新文档取代，导航等待层必然该撤。
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', onDocumentSettled, { once: true });
  } else {
    onDocumentSettled();
  }
  window.addEventListener('pagehide', resetLayer);

  if (retryButton) {
    retryButton.addEventListener('click', function () {
      resetLayer();
      if (pendingUrl && pendingUrl !== location.href) {
        location.href = pendingUrl;
      } else {
        location.reload();
      }
    });
  }

  /* ---------------- 3. 日期 sheet ---------------- */
  var sheet = document.getElementById('dateSheet');
  var sheetMask = document.getElementById('dateSheetMask');
  var sheetMode = 'day';
  var sheetYear = new Date().getFullYear();
  var sheetMonth = new Date().getMonth() + 1;
  var sheetToday = '';

  function syncSheetFields() {
    if (!sheet) { return; }
    var modeField = document.getElementById('sheetMode');
    var yearField = document.getElementById('sheetYear');
    var monthField = document.getElementById('sheetMonth');
    if (modeField) { modeField.value = sheetMode; }
    if (yearField) { yearField.value = sheetYear; }
    if (monthField) { monthField.value = sheetMonth; }
  }

  function renderSheet() {
    if (!sheet) { return; }
    syncSheetFields();
    Array.prototype.forEach.call(sheet.querySelectorAll('[data-sheet-mode]'), function (button) {
      button.classList.toggle('on', button.dataset.sheetMode === sheetMode);
    });
    Array.prototype.forEach.call(sheet.querySelectorAll('[data-panel]'), function (panel) {
      panel.hidden = panel.dataset.panel !== sheetMode;
    });
    Array.prototype.forEach.call(sheet.querySelectorAll('[data-sheet-year]'), function (button) {
      button.classList.toggle('on', Number(button.dataset.sheetYear) === Number(sheetYear));
    });
    Array.prototype.forEach.call(sheet.querySelectorAll('[data-sheet-month]'), function (button) {
      button.classList.toggle('on', Number(button.dataset.sheetMonth) === Number(sheetMonth));
    });
  }

  function openSheet() {
    if (!sheet) { return; }
    sheet.hidden = false;
    if (sheetMask) { sheetMask.hidden = false; }
    renderSheet();
  }

  function closeSheet() {
    if (!sheet) { return; }
    sheet.hidden = true;
    if (sheetMask) { sheetMask.hidden = true; }
  }

  if (sheet) {
    sheetMode = sheet.dataset.mode || 'day';
    sheetYear = Number(sheet.dataset.year) || sheetYear;
    sheetMonth = Number(sheet.dataset.month) || sheetMonth;
    sheetToday = sheet.dataset.today || '';

    document.addEventListener('click', function (event) {
      var target = event.target;
      if (!target || !target.closest) { return; }
      if (target.closest('[data-sheet-open]')) {
        event.preventDefault();
        openSheet();
        return;
      }
      if (target.closest('[data-sheet-close]')) {
        event.preventDefault();
        closeSheet();
        return;
      }
      var modeButton = target.closest('[data-sheet-mode]');
      if (modeButton) {
        sheetMode = modeButton.dataset.sheetMode;
        renderSheet();
        return;
      }
      var yearButton = target.closest('[data-sheet-year]');
      if (yearButton) {
        sheetYear = Number(yearButton.dataset.sheetYear);
        renderSheet();
        return;
      }
      var monthButton = target.closest('[data-sheet-month]');
      if (monthButton) {
        sheetMonth = Number(monthButton.dataset.sheetMonth);
        renderSheet();
        return;
      }
      var quickButton = target.closest('[data-sheet-quick]');
      if (quickButton) {
        applyQuick(quickButton.dataset.sheetQuick);
      }
    });
    renderSheet();
  }

  function setDayRange(value) {
    if (!sheet) { return; }
    var startInput = sheet.querySelector('input[name="start_date"]');
    var endInput = sheet.querySelector('input[name="end_date"]');
    if (startInput) { startInput.value = value; }
    if (endInput) { endInput.value = value; }
  }

  function applyQuick(kind) {
    if (!sheet) { return; }
    var today = sheetToday;
    var parts = (today || '').split('-');
    var year = parts.length === 3 ? Number(parts[0]) : new Date().getFullYear();
    var month = parts.length === 3 ? Number(parts[1]) : new Date().getMonth() + 1;

    if (kind === 'day') {
      sheetMode = 'day';
      sheetYear = year;
      sheetMonth = month;
      setDayRange(today);
    } else if (kind === 'month') {
      sheetMode = 'month';
      sheetYear = year;
      sheetMonth = month;
    } else if (kind === 'year') {
      sheetMode = 'year';
      sheetYear = year;
    } else {
      sheetMode = 'all';
    }
    renderSheet();
    // 快捷是「选了就走」：与设计稿一致，不需要再点确定。
    syncSheetFields();
    if (typeof sheet.requestSubmit === 'function') {
      sheet.requestSubmit();
    } else {
      sheet.submit();
    }
  }

  /* ---------------- 4. 下拉刷新（只在页面顶部接管手势） ---------------- */
  // 放在网页侧而不是壳侧：一套代码，桌面浏览器（无 touchstart）完全不注册。
  // 关键点：真正的滚动容器是 .body（.app 高度 100%，window 不滚动），
  // 所以判断「在顶部」必须看 .body.scrollTop —— 只看 window.scrollY 会永远为 0，
  // 列表滚到中间也照样触发下拉刷新。
  var PULL_THRESHOLD = 70;
  var PULL_LOCK_MS = 300;
  var scrollBox = document.querySelector('.body');
  var pullHint = null;
  var pulling = false;
  var pullStartY = null;
  var pullStartX = null;
  var pullDistance = 0;
  var lastRefreshAt = 0;
  var refreshing = false;
  var swallowClicksUntil = 0;

  // 下拉手势结束后浏览器仍可能补一个 click（从卡片上开始下拉、松手时正好落在卡片上），
  // 会让页面刚刷新又跳走。在 window 捕获阶段吞掉这次 click。
  window.addEventListener('click', function (event) {
    if (Date.now() < swallowClicksUntil) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);

  function atTop() {
    var boxTop = scrollBox ? scrollBox.scrollTop : 0;
    return (window.scrollY || 0) <= 0 && boxTop <= 0.5;
  }

  /* ---------------- 2.5 滚动位置记忆 ----------------
     滚动容器是 .body（window.scrollY 恒为 0），浏览器原生 scrollRestoration 对它无效，
     所以自己按 URL 记 scrollTop：返回列表时回到原位，前进到新页面从顶部开始。
     用 sessionStorage：只活在本次会话，关掉 WebView 不该记住柜台前滚到哪。 */
  var SCROLL_KEY = 'erp-mobile-scroll';

  function readScrollMap() {
    try {
      var raw = sessionStorage.getItem(SCROLL_KEY);
      var parsed = raw ? JSON.parse(raw) : null;
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (error) {
      return {};
    }
  }

  function saveScroll(url) {
    if (!scrollBox) { return; }
    // 不记 0：从顶部离开再回来，恢复成 0 与不恢复等价，但会让 map 无限膨胀。
    var top = scrollBox.scrollTop;
    if (!top || top < 1) { return; }
    try {
      var map = readScrollMap();
      map[url] = Math.round(top);
      sessionStorage.setItem(SCROLL_KEY, JSON.stringify(map));
    } catch (error) { /* 隐私模式：静默放弃记忆 */ }
  }

  function restoreScroll(url) {
    if (!scrollBox) { return; }
    var top = readScrollMap()[url];
    if (!top) { return; }
    // 为什么不能只 set 一两次：从 history 回到列表时，图片/字体还在加载，
    // .body 的 scrollHeight 远小于最终值，此时设 scrollTop 会被浏览器**夹成 0**
    // （实测：返回后 scrollTop 一直是 0，而 map 里的 1166 好好地存着）。
    // 做法：反复尝试若干次，直到真的设上了或超时；每次只在上一次没生效时继续。
    var attempts = 0;
    function attempt() {
      if (!scrollBox) { return; }
      if (Math.abs(scrollBox.scrollTop - top) < 2) { return; }  // 已到位
      scrollBox.scrollTop = top;
      attempts += 1;
      // 还没设上且没超次数：下一帧再试（容量不足时会自动被夹，见上）
      if (attempts < 40 && Math.abs(scrollBox.scrollTop - top) >= 2) {
        requestAnimationFrame(attempt);
      }
    }
    requestAnimationFrame(attempt);
  }

  function hint(text, ready) {
    if (!pullHint) { return; }
    pullHint.textContent = text;
    pullHint.classList.add('on');
    pullHint.classList.toggle('ready', !!ready);
  }

  function hideHint() {
    if (!pullHint) { return; }
    pullHint.classList.remove('on', 'ready');
  }

  function resetPull() {
    pulling = false;
    pullStartY = null;
    pullStartX = null;
    pullDistance = 0;
    hideHint();
  }

  function triggerRefresh() {
    if (refreshing || Date.now() - lastRefreshAt < PULL_LOCK_MS) { return; }
    lastRefreshAt = Date.now();
    refreshing = true;
    hint('正在刷新…');
    // 复用导航骨架，不自造第二套 loading。
    if (layer && skeleton && offline) {
      clearTimers();
      layer.hidden = false;
      layer.setAttribute('aria-hidden', 'false');
      skeleton.hidden = false;
      offline.hidden = true;
    }
    setTimeout(function () { location.reload(); }, 260);
  }

  if ('ontouchstart' in window) {
    pullHint = document.createElement('div');
    pullHint.className = 'pull-hint';
    pullHint.setAttribute('aria-hidden', 'true');
    document.body.appendChild(pullHint);

    document.addEventListener('touchstart', function (event) {
      if (event.touches.length !== 1 || refreshing) { return; }
      if (Date.now() - lastRefreshAt < PULL_LOCK_MS) { return; }
      // 日期 sheet 打开时不动页面
      var sheetOpen = sheet && !sheet.hidden;
      if (sheetOpen || !atTop()) { return; }
      pullStartX = event.touches[0].clientX;
      pullStartY = event.touches[0].clientY;
      pullDistance = 0;
      pulling = false;
    }, { passive: true });

    document.addEventListener('touchmove', function (event) {
      if (pullStartY === null || event.touches.length !== 1 || refreshing) { return; }
      var dy = event.touches[0].clientY - pullStartY;
      var dx = event.touches[0].clientX - pullStartX;
      if (!pulling) {
        // 向上滑 / 看起来是横滑 / 已经不在顶部：完全不接管，交给列表滚动。
        if (dy <= 0 || Math.abs(dx) > Math.abs(dy) || !atTop()) {
          resetPull();
          return;
        }
        pulling = true;
      }
      if (event.cancelable) { event.preventDefault(); }
      pullDistance = dy;
      var ready = dy >= PULL_THRESHOLD;
      hint(ready ? '松手刷新' : '下拉刷新', ready);
    }, { passive: false });

    document.addEventListener('touchend', function () {
      if (pullStartY === null) { return; }
      var distance = pullDistance;
      var fired = pulling && distance >= PULL_THRESHOLD;
      if (pulling) { swallowClicksUntil = Date.now() + 400; }
      resetPull();
      if (fired) { triggerRefresh(); }
    }, { passive: true });

    document.addEventListener('touchcancel', resetPull, { passive: true });
  }

  /* ---------------- 5. 外观 + 闲置回首页 ---------------- */
  function readTheme() {
    try {
      var saved = localStorage.getItem(THEME_KEY);
      if (saved === 'light' || saved === 'dark' || saved === 'system') { return saved; }
    } catch (error) { /* 隐私模式 */ }
    return 'system';
  }

  function applyTheme(value) {
    if (value !== 'light' && value !== 'dark' && value !== 'system') { value = 'system'; }
    document.documentElement.setAttribute('data-theme', value);
    try { localStorage.setItem(THEME_KEY, value); } catch (error) { /* 忽略 */ }
    Array.prototype.forEach.call(document.querySelectorAll('[data-theme-set]'), function (button) {
      button.classList.toggle('on', button.dataset.themeSet === value);
    });
    syncChromeColor(value);
  }

  /* ---------------- 5.1 主题色同步 ----------------
     两套消费方，各喂各的：
     - 浏览器（Chrome / 微信扫码）：改 <meta name="theme-color">，地址栏跟着变。
     - APK 壳：原生 applyWindowChrome() 只在 onCreate 跑一次，且只读系统夜间模式，
       壳根本不知道页面内切了主题 → 深色页面配浅色状态栏。这里显式通知原生重设。
     深色的判定：显式 dark，或「跟随系统」且系统当前是深色。 */
  function resolvedDark(value) {
    if (value === 'dark') { return true; }
    if (value === 'light') { return false; }
    return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  function syncChromeColor(value) {
    var dark = resolvedDark(value);
    var meta = document.getElementById('themeColorMeta');
    if (meta) { meta.setAttribute('content', dark ? '#0b0f16' : '#fbfbfd'); }
    var bridge = window.SimpleERPNative;
    if (bridge && typeof bridge.applyDarkMode === 'function') {
      bridge.applyDarkMode(dark);
    }
  }

  // 「跟随系统」时系统深浅变了也要跟上（浏览器场景改 meta；APK 壳会自己重建，这里只管 meta）。
  if (window.matchMedia) {
    var darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
    var onSchemeChange = function () { syncChromeColor(readTheme()); };
    if (darkQuery.addEventListener) { darkQuery.addEventListener('change', onSchemeChange); }
    else if (darkQuery.addListener) { darkQuery.addListener(onSchemeChange); }
  }

  var themeButtons = document.querySelectorAll('[data-theme-set]');
  if (themeButtons.length) {
    applyTheme(readTheme());
    Array.prototype.forEach.call(themeButtons, function (button) {
      button.addEventListener('click', function () { applyTheme(button.dataset.themeSet); });
    });
  }

  /* ---------------- 5.4 客户详情页：区块开合 ----------------
     专属价 / 最近流水默认露 3 条，第 4 条起带 .expand-hidden。
     点按钮就地展开，**再点收起**（2026-10-18 用户要求可逆，之前只做了单向展开，
     而且展开后文案还写着「展开全部」，等于骗人）。
     收起时的滚动处理：列表在按钮上方，收掉一截会把下面的内容顶上来，
     如果用户正看着列表中部就会被甩走 —— 所以在收起前后量一次 scrollTop 差值再补回去。
     不用 scrollIntoView 之类的，那会把按钮钉死在视口顶部，反而更跳。 */
  Array.prototype.forEach.call(document.querySelectorAll('[data-expand-btn]'), function (button) {
    var expanded = false;
    var moreText = button.getAttribute('data-more-text') || '';
    var lessText = button.getAttribute('data-less-text') || '收起';

    button.addEventListener('click', function () {
      // 按钮的前一个兄弟就是它负责的那个卡片（价格卡片 / 流水卡片）。
      var card = button.previousElementSibling;
      if (!card || !card.querySelectorAll) { return; }

      var box = document.querySelector('.body');
      var beforeTop = box ? box.scrollTop : 0;

      expanded = !expanded;
      Array.prototype.forEach.call(card.querySelectorAll('[data-expand-item]'), function (item) {
        item.classList.toggle('expand-hidden', !expanded);
      });
      button.textContent = expanded ? lessText : moreText;

      // 收起后内容变短，scrollTop 会被浏览器自动夹小；把被夹掉的部分补回来，
      // 让「按钮以上的内容」留在用户原本看的位置。
      if (!expanded && box) {
        requestAnimationFrame(function () {
          box.scrollTop = Math.min(beforeTop, Math.max(0, box.scrollHeight - box.clientHeight));
        });
      }
    });
  });

  /* ---------------- 5.5 tab 预取 ----------------
     每次点 tab 都是整页导航，要重新握手 + 拿 HTML + 拿 CSS/JS。
     实测冷连接约 600ms、连接热了之后约 20ms，所以真正的杠杆是「别等用户点了才开始连」。
     这里在页面空闲（requestIdleCallback）时，用 <link rel="prefetch"> 让浏览器
     预先建好连接并把目标页拿进 HTTP 缓存。点下去时直接命中，不再等握手。
     只预取底部 5 个 tab，不预取详情页（详情页是列表点出来的，数量不定、预取会浪费）。*/
  function prefetchTabs() {
    var tabs = document.querySelectorAll('.tabbar a[href]');
    if (!tabs.length) { return; }
    var current = location.pathname;
    Array.prototype.forEach.call(tabs, function (link) {
      var href = link.getAttribute('href');
      if (!href || href === current) { return; }
      // 同一地址不重复插：SPA 式前进后退可能反复触发。
      if (document.querySelector('link[rel="prefetch"][href="' + href + '"]')) { return; }
      var tag = document.createElement('link');
      tag.rel = 'prefetch';
      tag.href = href;
      tag.as = 'document';
      document.head.appendChild(tag);
    });
  }

  // requestIdleCallback 不可用时退回 setTimeout：首屏渲染完再预取，不抢带宽。
  if (window.requestIdleCallback) {
    window.requestIdleCallback(prefetchTabs, { timeout: 2000 });
  } else {
    setTimeout(prefetchTabs, 800);
  }

  /* ---------------- 6. 设置页：改电脑地址（只在 APK 原生桥存在时生效） ---------------- */
  // 旧版壳右下角有个齿轮改地址，和「设置」tab 重复；现在收进设置页这一行。
  // 浏览器里没有 SimpleERPNative，这行保持静态，不出现「点这里修改」的假入口。
  var nativeBridge = window.SimpleERPNative;
  var canOpenAddress = nativeBridge && typeof nativeBridge.openAddressDialog === 'function';
  if (canOpenAddress) {
    var addressRow = document.getElementById('addressRow');
    if (addressRow) {
      addressRow.classList.add('act');
      addressRow.setAttribute('role', 'button');
      addressRow.setAttribute('tabindex', '0');
      var addressHint = document.getElementById('addressHint');
      var addressChev = document.getElementById('addressChev');
      if (addressHint) { addressHint.hidden = false; }
      if (addressChev) { addressChev.hidden = false; }
      addressRow.addEventListener('click', function () { nativeBridge.openAddressDialog(); });
      addressRow.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          nativeBridge.openAddressDialog();
        }
      });
    }
    // 配对页没有底部 tab，也没有齿轮；地址不对时必须还能改。
    var pairAddress = document.getElementById('pairChangeAddress');
    if (pairAddress) {
      pairAddress.hidden = false;
      pairAddress.addEventListener('click', function () { nativeBridge.openAddressDialog(); });
    }
  }

  // 闲置回首页：柜台放下一段时间后自动回到今日概览，下一位客人不用看到上一位的客户详情。
  var IDLE_LIMIT = 5 * 60 * 1000;
  var idleTimer = null;
  function resetIdle() {
    if (idleTimer) { clearTimeout(idleTimer); }
    idleTimer = setTimeout(function () {
      if (document.hidden) { resetIdle(); return; }
      var home = document.body.getAttribute('data-home') || '/m/';
      location.href = home;
    }, IDLE_LIMIT);
  }
  ['touchstart', 'click', 'keydown', 'scroll'].forEach(function (name) {
    document.addEventListener(name, resetIdle, { passive: true });
  });
  resetIdle();
})();
