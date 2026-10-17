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

  function resetLayer() {
    clearTimers();
    leaving = false;
    if (layer) {
      layer.hidden = true;
      layer.setAttribute('aria-hidden', 'true');
    }
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

  window.addEventListener('pageshow', resetLayer);
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
  }

  var themeButtons = document.querySelectorAll('[data-theme-set]');
  if (themeButtons.length) {
    applyTheme(readTheme());
    Array.prototype.forEach.call(themeButtons, function (button) {
      button.addEventListener('click', function () { applyTheme(button.dataset.themeSet); });
    });
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
