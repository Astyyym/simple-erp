/*
 * 关闭软件前的「先让手机同步一下」浮层（2026-10-24 用户要求：每次必弹）。
 *
 * 为什么是页内浮层、不是 Windows 原生弹窗：
 * 原生弹窗只能放一段纯文本，放不下**实时同步时间**和二维码 —— 而这两样恰好是
 * 这个提醒唯一有价值的信息（「现在关，手机上能看到到几点的数据」）。
 * 页内浮层还能做「过几秒自动刷新」：店主走到手机那边点同步，回来时这层里的
 * 时间已经自己变成「刚刚同步」了，不用他再刷新页面。
 *
 * 与 Python 侧的契约（`desktop_app.CloseGate`）：
 * - `window.erpClosePrompt()` 必须是**同步**返回 true 的函数：pywebview 的
 *   `evaluate_js` 不会等 Promise，返回 Promise 会被当成「没弹出来」，于是走兜底。
 *   所以这里是「先挂上骨架、再异步填数据」，不是 async 函数。
 * - 失败（找不到挂载点/异常）要返回 false，让 Python 走原生确认框兜底 ——
 *   绝不能让「提醒失败」变成「程序关不掉」。
 */
(function () {
  'use strict';

  var OVERLAY_ID = 'erpClosePromptOverlay';
  var STYLE_ID = 'erpClosePromptStyle';
  var pollTimer = null;

  // 状态接口地址由 base.html 通过 <meta> 注入；取不到就用固定路径（同一个蓝图）。
  function stateUrl() {
    var meta = document.querySelector('meta[name="erp-close-state-url"]');
    return (meta && meta.content) || '/settings/mobile/close-state';
  }

  function bridge() {
    return (window.pywebview && window.pywebview.api) || null;
  }

  function callBridge(name, arg) {
    var api = bridge();
    if (!api || typeof api[name] !== 'function') return false;
    try {
      api[name](arg);
      return true;
    } catch (error) {
      return false;
    }
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  /** 秒 → 「刚刚 / N 分钟前 / N 小时前」。同步时间只有相对表述才对人有意义。 */
  function agoText(seconds) {
    if (seconds === null || seconds === undefined) return '';
    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return Math.floor(seconds / 60) + ' 分钟前';
    if (seconds < 86400) return Math.floor(seconds / 3600) + ' 小时前';
    return Math.floor(seconds / 86400) + ' 天前';
  }

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = [
      '#' + OVERLAY_ID + '{position:fixed;inset:0;z-index:99999;display:flex;',
      'align-items:center;justify-content:center;padding:24px;',
      'background:rgba(15,20,29,.55);-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px)}',
      '#' + OVERLAY_ID + ' .erp-cp-card{width:min(600px,100%);max-height:calc(100vh - 48px);overflow:auto;',
      'background:var(--erp-surface,#fff);color:var(--erp-ink,#172033);',
      'border:1px solid var(--erp-border,#dfe3ea);border-radius:14px;',
      'box-shadow:0 24px 64px rgba(16,24,40,.28);padding:22px 24px 18px}',
      '#' + OVERLAY_ID + ' h3{margin:0 0 6px;font-size:18px;font-weight:650;letter-spacing:-.01em}',
      '#' + OVERLAY_ID + ' .erp-cp-sub{margin:0 0 14px;font-size:13px;line-height:1.65;color:var(--erp-muted,#667085)}',
      '#' + OVERLAY_ID + ' .erp-cp-status{border:1px solid var(--erp-border,#dfe3ea);border-radius:10px;',
      'padding:12px 14px;margin:0 0 14px;background:var(--erp-surface-soft,#f8fafc)}',
      '#' + OVERLAY_ID + ' .erp-cp-status.is-stale{border-color:#fcd34d;background:#fffbeb;color:#92400e}',
      '#' + OVERLAY_ID + ' .erp-cp-big{font-size:15px;font-weight:650;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}',
      '#' + OVERLAY_ID + ' .erp-cp-ago{font-size:12.5px;font-weight:500;opacity:.85}',
      '#' + OVERLAY_ID + ' .erp-cp-devices{list-style:none;margin:10px 0 0;padding:0}',
      '#' + OVERLAY_ID + ' .erp-cp-devices li{display:flex;align-items:center;gap:8px;flex-wrap:wrap;',
      'padding:6px 0;border-top:1px dashed var(--erp-border,#dfe3ea);font-size:12.5px}',
      '#' + OVERLAY_ID + ' .erp-cp-devices li:first-child{border-top:0}',
      '#' + OVERLAY_ID + ' .erp-cp-devices .erp-cp-name{font-weight:600}',
      '#' + OVERLAY_ID + ' .erp-cp-devices code{font-size:11.5px;color:var(--erp-muted,#667085)}',
      '#' + OVERLAY_ID + ' .erp-cp-devices .erp-cp-when{margin-left:auto;white-space:nowrap;color:var(--erp-muted,#667085)}',
      '#' + OVERLAY_ID + ' .erp-cp-devices .erp-cp-when.is-ok{color:var(--erp-success,#17803d);font-weight:600}',
      '#' + OVERLAY_ID + ' .erp-cp-how{margin:0 0 14px;padding:0 0 0 18px;font-size:13px;line-height:1.75;color:var(--erp-ink,#172033)}',
      '#' + OVERLAY_ID + ' .erp-cp-how b{font-weight:650}',
      '#' + OVERLAY_ID + ' .erp-cp-connect{display:flex;align-items:center;gap:14px;margin:0 0 16px;',
      'padding-top:12px;border-top:1px solid var(--erp-border,#dfe3ea)}',
      '#' + OVERLAY_ID + ' .erp-cp-connect img{width:104px;height:104px;border:1px solid var(--erp-border,#dfe3ea);',
      'border-radius:8px;padding:6px;background:#fff;flex:0 0 auto}',
      '#' + OVERLAY_ID + ' .erp-cp-connect .erp-cp-url{font-size:12px;line-height:1.7;color:var(--erp-muted,#667085);word-break:break-all}',
      '#' + OVERLAY_ID + ' .erp-cp-actions{display:flex;align-items:center;gap:10px;justify-content:flex-end;flex-wrap:wrap}',
      '#' + OVERLAY_ID + ' .erp-cp-actions .btn{min-width:104px}',
      '#' + OVERLAY_ID + ' .erp-cp-actions .erp-cp-cancel{margin-right:auto;background:transparent;border-color:transparent;color:var(--erp-muted,#667085)}',
      '#' + OVERLAY_ID + ' .erp-cp-actions .erp-cp-cancel:hover{background:var(--erp-surface-soft,#f8fafc);color:var(--erp-ink,#172033)}',
    ].join('');
    document.head.appendChild(style);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function closeOverlay() {
    stopPolling();
    var node = document.getElementById(OVERLAY_ID);
    if (node && node.parentNode) node.parentNode.removeChild(node);
  }

  function confirmClose() {
    stopPolling();
    // 先摘掉浮层：destroy 有几十毫秒的空档，留着半透明遮罩看着像卡住。
    closeOverlay();
    callBridge('erp_close_window');
  }

  function cancelClose() {
    closeOverlay();
    // 必须告诉后端「用户取消了」，否则下一次点 × 会被当成「第二次点、直接放行」，
    // 于是提醒只在第一次出现 —— 而用户要的是**每次**都提醒。
    callBridge('erp_close_prompt_cancel');
  }

  function renderDevices(devices) {
    if (!devices || !devices.length) return '';
    var rows = devices.map(function (d) {
      var uid = d.uid_short
        ? '<code title="' + escapeHtml(d.uid || '') + '">' + escapeHtml(d.uid_short) + '</code>'
        : '<code title="升级前配对的旧记录，没有设备 ID">旧记录</code>';
      var when = d.last_sync_seconds === null || d.last_sync_seconds === undefined
        ? '<span class="erp-cp-when">从未同步</span>'
        : '<span class="erp-cp-when is-ok">已同步 ' + escapeHtml(agoText(d.last_sync_seconds)) + '</span>';
      return '<li><span class="erp-cp-name">' + escapeHtml(d.label || '手机') + '</span>'
        + '<span style="opacity:.7">' + escapeHtml(d.platform_label || '') + '</span>'
        + uid + when + '</li>';
    }).join('');
    return '<ul class="erp-cp-devices">' + rows + '</ul>';
  }

  function paint(payload) {
    var status = document.getElementById('erpCloseStatus');
    if (!status) return;

    /*
     * 监听没起来（端口被上一次没退干净的进程占着时，开关会回弹、监听不启动）。
     *
     * 这时**绝对不能**让店主拿起手机去同步 —— 那一定失败，而他会以为是手机坏了。
     * 所以整块换成「这里有问题」+ 怎么修，并隐藏那三步指引与二维码。
     * 主按钮也要跟着改口：此时「我已同步」是不可能做到的动作，写着就是骗人。
     */
    var listening = payload.listening !== false;
    var how = document.getElementById('erpCloseHow');
    var connect = document.getElementById('erpCloseConnect');
    var headline = document.getElementById('erpCloseHeadline');
    var subtitle = document.getElementById('erpCloseSubtitle');
    var primary = document.getElementById('erpCloseNow');
    if (!listening) {
      if (headline) headline.textContent = '手机访问没在运行';
      if (subtitle) {
        subtitle.textContent =
          '这次关电脑之前手机同步不了，手机上会停在上一次同步的数据。不影响你现在关闭。';
      }
      if (primary) primary.textContent = '知道了，关闭软件';
      status.className = 'erp-cp-status is-stale';
      status.innerHTML =
        '<div class="erp-cp-big">手机端服务没有起来</div>'
        + '<div style="font-size:12.5px;line-height:1.7;margin-top:6px">'
        + escapeHtml(payload.listening_error || '端口被占用，或这次启动没起来。')
        + '<br>修法：完全退出程序后重新打开；仍不行就在「设置 → 手机访问」看诊断。</div>';
      if (how) how.style.display = 'none';
      if (connect) connect.style.display = 'none';
      return;
    }
    if (headline) headline.textContent = '关门之前，先让手机同步一下';
    if (subtitle) {
      subtitle.textContent =
        '手机同步一次之后，这台电脑关掉也能在手机上翻单据、客户和商品价。'
        + '没有同步就关，手机上看到的还是上一次的数据。';
    }
    if (primary) primary.textContent = '我已同步，关闭软件';
    if (how) how.style.display = '';
    if (connect) connect.style.display = '';

    var devices = payload.devices || [];
    var stale = payload.last_sync_seconds === null || payload.last_sync_seconds === undefined
      || payload.last_sync_seconds > 1800;
    status.className = 'erp-cp-status' + (stale ? ' is-stale' : '');
    var head = (payload.last_sync_seconds === null || payload.last_sync_seconds === undefined)
      ? '这台电脑还没有被手机完整同步过'
      : '最后一次同步：' + (payload.last_sync_label || '') + '（' + agoText(payload.last_sync_seconds) + '）';
    status.innerHTML = '<div class="erp-cp-big">' + escapeHtml(head)
      + (stale ? '<span class="erp-cp-ago">建议现在同步一次再关</span>' : '')
      + '</div>' + renderDevices(devices);

    var urlNode = document.getElementById('erpCloseUrl');
    if (urlNode) urlNode.textContent = payload.url || '';
    var qrNode = document.getElementById('erpCloseQr');
    if (qrNode) {
      if (payload.qr) {
        qrNode.src = payload.qr;
        qrNode.style.display = '';
      } else {
        qrNode.style.display = 'none';
      }
    }
  }

  function refresh() {
    fetch(stateUrl(), { headers: { Accept: 'application/json' }, cache: 'no-store' })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (payload) { if (payload && payload.ok) paint(payload); })
      .catch(function () { /* 网络抖动就跳过这一轮，浮层保持上一帧 */ });
  }

  /**
   * 挂上浮层并立刻返回 true（**同步**，见文件头契约）。
   * 重复调用（比如用户点了两次 ×）只重画一次，不叠两层。
   */
  window.erpClosePrompt = function () {
    try {
      injectStyle();
      closeOverlay();
      var overlay = document.createElement('div');
      overlay.id = OVERLAY_ID;
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-modal', 'true');
      overlay.innerHTML = [
        '<div class="erp-cp-card">',
        '  <h3 id="erpCloseHeadline">关门之前，先让手机同步一下</h3>',
        '  <p class="erp-cp-sub" id="erpCloseSubtitle">手机同步一次之后，这台电脑关掉也能在手机上翻单据、客户和商品价。',
        '    没有同步就关，手机上看到的还是上一次的数据。</p>',
        '  <div class="erp-cp-status" id="erpCloseStatus"><div class="erp-cp-big">正在读取同步状态…</div></div>',
        '  <ol class="erp-cp-how" id="erpCloseHow">',
        '    <li>拿起手机，打开 <b>简单ERP</b> App</li>',
        '    <li>点右下角 <b>设置</b> → <b>立即同步</b>（几秒就好）</li>',
        '    <li>这里的时间会自己变成「刚刚」，然后就可以放心关电脑了</li>',
        '  </ol>',
        '  <div class="erp-cp-connect" id="erpCloseConnect">',
        '    <img id="erpCloseQr" alt="手机访问二维码" style="display:none">',
        '    <div class="erp-cp-url">手机连的是这台电脑：<br><span id="erpCloseUrl">—</span><br>',
        '      手机 App 里地址不对时，扫左边的码重新配对。</div>',
        '  </div>',
        '  <div class="erp-cp-actions">',
        '    <button type="button" class="btn erp-cp-cancel" id="erpCloseCancel">取消，先不关</button>',
        '    <button type="button" class="btn btn-outline-secondary" id="erpCloseAnyway">不同步，直接关闭</button>',
        '    <button type="button" class="btn btn-primary" id="erpCloseNow">我已同步，关闭软件</button>',
        '  </div>',
        '</div>',
      ].join('');
      document.body.appendChild(overlay);

      overlay.querySelector('#erpCloseCancel').addEventListener('click', cancelClose);
      overlay.querySelector('#erpCloseAnyway').addEventListener('click', confirmClose);
      overlay.querySelector('#erpCloseNow').addEventListener('click', confirmClose);
      // Esc = 取消（不关），与「取消，先不关」同一语义。
      overlay.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
          event.stopPropagation();
          cancelClose();
        }
      });
      overlay.querySelector('#erpCloseNow').focus();

      refresh();
      // 每 3 秒刷一次：店主走到手机旁点同步，回来时这层已经把时间更新成「刚刚」。
      stopPolling();
      pollTimer = setInterval(refresh, 3000);
      return true;
    } catch (error) {
      return false;
    }
  };
})();
