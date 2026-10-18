/* Shared order-entry helpers for all four entry pages (and, from Phase 3, the list pages).
   Single source of truth: the sales page used to carry an inline copy of these functions. */
(function () {
  'use strict';

  function money(n) {
    return (Math.round((Number(n) || 0) * 100) / 100).toFixed(2);
  }

  function closeSuggestions(input, box) {
    if (box.hasAttribute('popover') && box.matches(':popover-open')) box.hidePopover();
    box.classList.add('d-none');
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    box.querySelectorAll('[role="option"]').forEach(option => option.setAttribute('aria-selected', 'false'));
  }

  function showSuggestions(input, box, items, render, onPick, emptyText = '无匹配项', listMaxHeight = 220) {
    box.innerHTML = '';
    if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'suggest-item text-muted';
      empty.textContent = emptyText;
      empty.setAttribute('role', 'option');
      empty.setAttribute('aria-disabled', 'true');
      box.appendChild(empty);
    } else items.forEach((item, index) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'suggest-item';
      option.id = `${box.id}_option_${index}`;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', 'false');
      option.appendChild(render(item));
      option.addEventListener('mousedown', event => event.preventDefault());
      option.addEventListener('click', () => { onPick(item); closeSuggestions(input, box); });
      box.appendChild(option);
    });
    box.classList.remove('d-none');
    if (typeof box.showPopover === 'function') {
      box.setAttribute('popover', 'manual');
      const anchor = input.getBoundingClientRect();
      const below = Math.max(0, window.innerHeight - anchor.bottom - 12);
      const above = Math.max(0, anchor.top - 12);
      const upwards = below < 120 && above > below;
      box.style.width = `${anchor.width}px`;
      box.style.left = `${anchor.left}px`;
      box.style.maxHeight = `${Math.min(listMaxHeight, upwards ? above : below)}px`;
      if (!box.matches(':popover-open')) box.showPopover();
      box.style.left = `${Math.max(12, Math.min(anchor.left, window.innerWidth - box.offsetWidth - 12))}px`;
      box.style.top = `${upwards ? anchor.top - box.offsetHeight - 4 : anchor.bottom + 4}px`;
    }
    input.setAttribute('aria-expanded', 'true');
    input.removeAttribute('aria-activedescendant');
  }

  // Generic combobox: loadItems(query) -> array; renderOption(item) -> node; onPick(item).
  // emptyText: 无匹配时的文案（零建档路径的页面应给出「保存时会新建」而不是断言性的「无匹配项」）。
  // options.listOnEmpty: 空值时也拉候选（默认关闭）——供「点击输入框就列出最近单据」的字段使用；
  //   关闭时保持原行为：空值立即关闭候选，避免给客户/商品这类高频字段无谓地多打一次请求。
  // options.maxHeight: 候选列表最大高度（px，默认 220）——候选很多时由调用方收紧，内部仍可滚动。
  // options.openOnClick: 已选中后再点输入框也重新列候选（默认关闭）。
  //   必要性（F3，2026-10-14）：候选按钮的 mousedown 被 preventDefault，点选后**焦点仍留在输入框**，
  //   于是「再点一次输入框」不会产生 focus 事件 → 候选再也打不开，用户以为「选完就锁死了」。
  //   且此时输入框里是已选项的标签，按它过滤只会剩它自己，所以命中「刚选过的那一项」时按空查询列全部候选。
  function bindAutocomplete(input, box, loadItems, renderOption, onPick, onInputChange = () => {}, emptyText = '无匹配项', options = {}) {
    // P3：判重用元素上的 JS 属性（cloneNode 只复制 attributes，不复制 JS 属性），
    // 这样克隆出来的新行能重新绑定，同一元素重复调用仍直接跳过。
    if (input.__erpAutocompleteBound) return;
    input.__erpAutocompleteBound = true;
    // 克隆行会带出上一行的浮层 id 与展开态：id 冲突就换新的，并收起。
    const idOwner = typeof document.getElementById === 'function' ? document.getElementById(box.id) : box;
    if (!box.id || idOwner !== box) box.id = `suggestBox${Math.random().toString(36).slice(2, 8)}`;
    if (box.hasAttribute && box.hasAttribute('popover') && box.matches && box.matches(':popover-open')) box.hidePopover();
    box.classList.add('d-none');
    const listOnEmpty = options.listOnEmpty === true;
    const listMaxHeight = Number(options.maxHeight) > 0 ? Number(options.maxHeight) : 220;
    let matches = [];
    let activeIndex = -1;
    let requestSequence = 0;
    // 刚选过的那一项在输入框里的标签：再点输入框时按空查询列全部候选（见 options.openOnClick）。
    let pickedLabel = '';
    // 中文输入法组合期间的 value 是拼音串，此时查询与渲染都会误导用户（看到假的「无匹配项」）。
    let composing = false;
    const isOpen = () => !box.classList.contains('d-none');
    const close = () => {
      requestSequence += 1;
      closeSuggestions(input, box);
      activeIndex = -1;
    };
    const setActive = index => {
      const options = [...box.querySelectorAll('[role="option"]:not([aria-disabled="true"])')];
      if (!options.length) return;
      activeIndex = Math.max(0, Math.min(index, options.length - 1));
      options.forEach((option, optionIndex) => option.setAttribute('aria-selected', String(optionIndex === activeIndex)));
      input.setAttribute('aria-activedescendant', options[activeIndex].id);
      options[activeIndex].scrollIntoView({ block: 'nearest' });
    };
    const choose = item => { onPick(item); pickedLabel = input.value.trim(); close(); };
    // F5（2026-10-14）：打开候选只有「按当前值过滤」一条规则时，选完之后输入框里就是
    // 已选那张单的标签 → 再点只会列出它自己。真人点击是 mousedown→focus→mouseup→click，
    // focus 通常先到，于是**第一次点击**看到的就是「只有已选那一张」，而接口慢时又正常
    // （表现为时好时坏）。现在 focus 与 click 共用这一条：刚选过的那一项按空查询列全部候选。
    // 只对 options.openOnClick 的字段生效——客户/商品这类高频字段保持原行为（不多打请求）。
    const queryOnOpen = () => {
      const current = input.value.trim();
      if (options.openOnClick === true && current && current === pickedLabel) return '';
      return current;
    };
    const refresh = async (query = input.value.trim(), initialDirection = '') => {
      const sequence = ++requestSequence;
      try {
        const result = await loadItems(query);
        if (sequence !== requestSequence) return;
        matches = Array.isArray(result) ? result : [];
        showSuggestions(input, box, matches, renderOption, choose, emptyText, listMaxHeight);
        if (matches.length && initialDirection === 'down') setActive(0);
        else if (matches.length && initialDirection === 'up') setActive(matches.length - 1);
      } catch (_) {
        if (sequence === requestSequence) { matches = []; close(); }
      }
    };

    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', box.id);
    input.setAttribute('aria-haspopup', 'listbox');
    input.addEventListener('input', event => {
      // 组合期间不查询、不渲染（上屏后由 compositionend 统一处理）。
      if (composing || event.isComposing) return;
      onInputChange();
      const query = input.value.trim();
      if (!query) {
        if (listOnEmpty) { refresh(''); return; }
        requestSequence += 1; matches = []; close(); return;
      }
      refresh(query);
    });
    input.addEventListener('compositionstart', () => { composing = true; });
    input.addEventListener('compositionend', () => {
      composing = false;
      onInputChange();
      const query = input.value.trim();
      if (!query) {
        if (listOnEmpty) { refresh(''); return; }
        requestSequence += 1; matches = []; close(); return;
      }
      refresh(query);
    });
    input.addEventListener('focus', () => {
      if (input.value.trim() || listOnEmpty) refresh(queryOnOpen());
    });
    // F3：已选中后再点输入框也要能重新列候选（点选后焦点没离开输入框，focus 不会再次触发）。
    if (options.openOnClick === true) {
      input.addEventListener('click', () => {
        if (isOpen()) return;
        refresh(queryOnOpen());
      });
    }
    input.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (!isOpen()) { refresh(input.value.trim(), event.key === 'ArrowDown' ? 'down' : 'up'); return; }
        const nextIndex = event.key === 'ArrowDown'
          ? (activeIndex < 0 ? 0 : activeIndex + 1)
          : (activeIndex < 0 ? matches.length - 1 : activeIndex - 1);
        setActive(nextIndex);
        return;
      }
      if (event.key === 'Escape' && isOpen()) { event.preventDefault(); close(); return; }
      if (event.key === 'Enter' && isOpen()) {
        event.preventDefault();
        if (activeIndex >= 0 && matches[activeIndex]) choose(matches[activeIndex]);
      }
    });
    input.addEventListener('blur', () => window.setTimeout(() => {
      if (document.activeElement !== input) close();
    }, 120));
    input._closeSuggestions = close;
  }

  // 滚动/缩放时让候选框跟随自己的输入框；输入框真的滚出视口才关闭。四页共用这一份。
  function closeFloatingProductSuggestions(event) {
    document.querySelectorAll('.suggest-list[popover]').forEach(box => {
      if (!box.matches(':popover-open')) return;
      // 在候选框内部滚动不算「表单被滚动」，保留候选。
      if (event.type === 'scroll' && box.contains(event.target)) return;
      const input = document.querySelector(`[aria-controls="${box.id}"]`);
      if (event.type !== 'scroll') {
        // 视口尺寸变了：位置不再可信，直接关闭。
        input?._closeSuggestions?.();
        return;
      }
      const anchor = input?.getBoundingClientRect();
      // 输入框滚出视口才关闭；否则重算位置跟随，避免「滚 1px 就消失」。
      if (!anchor || anchor.bottom < 0 || anchor.top > window.innerHeight) {
        input?._closeSuggestions?.();
        return;
      }
      const below = Math.max(0, window.innerHeight - anchor.bottom - 12);
      const above = Math.max(0, anchor.top - 12);
      const upwards = below < 120 && above > below;
      box.style.left = `${Math.max(12, Math.min(anchor.left, window.innerWidth - box.offsetWidth - 12))}px`;
      box.style.top = `${upwards ? anchor.top - box.offsetHeight - 4 : anchor.bottom + 4}px`;
    });
  }

  /* 单位单元格（P2，2026-10-13）：单控件 = 输入框 + 候选浮层，四页统一。
     候选池 = 模板服务端渲染的 data-unit-options（库内单位 ∪ 默认三项）
             + 本页手输过的新单位（unitExtras，跨行共享，不必等保存）。
     单位本来就允许自由输入，所以没有匹配项时不展示「无匹配项」断言。 */
  const unitExtras = new Set();
  let unitBoxSeq = 0;

  /* 克隆行只复制 attributes，不复制 JS 属性 —— 判重就用元素上的 JS 属性：
     克隆出来的新行没有这个标记，会重新绑定；同一元素重复调用则直接跳过。 */
  function bindUnitCell(input, box) {
    if (!input || !box || input.__erpUnitCellBound) return;
    input.__erpUnitCellBound = true;
    // 克隆行会带出上一行的 id / 展开态：id 冲突就换新的，并收起浮层。
    if (!box.id || document.getElementById(box.id) !== box) box.id = `unitSuggestions${(unitBoxSeq += 1)}`;
    if (box.hasAttribute('popover') && box.matches(':popover-open')) box.hidePopover();
    box.classList.add('d-none');
    input.setAttribute('aria-controls', box.id);
    const base = String(input.dataset.unitOptions || '').split('|').map(part => part.trim()).filter(Boolean);
    bindAutocomplete(
      input,
      box,
      query => {
        const pool = [];
        const seen = new Set();
        base.concat(Array.from(unitExtras)).forEach(unit => {
          if (unit && !seen.has(unit)) { seen.add(unit); pool.push(unit); }
        });
        const needle = String(query || '').trim();
        return needle ? pool.filter(unit => unit.indexOf(needle) >= 0) : pool;
      },
      unit => {
        const node = document.createElement('span');
        node.textContent = unit;
        return node;
      },
      unit => { input.value = unit; },
      () => { const value = input.value.trim(); if (value) unitExtras.add(value); },
      '',
      { listOnEmpty: true, maxHeight: 200 },
    );
  }

  /* 往来对象（hidden `customer_id`）变化监听（2026-10-15，修复「换客户后单价不跟」）。
     为什么不是 addEventListener('change')：hidden 输入框上的赋值有三类来源——
       1. 候选点选 / 手输清空（bindAutocomplete 的 onPick / onInputChange）→ 直接 `.value =`，无事件；
       2. `syncCounterparty` 关联原单带出对象 → 同样直接赋值，无事件；
       3. 用户改名字但没命中候选 → 也只写 value。
     `input`/`change` 只在**用户交互**的元素上触发，写 hidden 的 value 既不触发事件、
     MutationObserver 也不监视 value 属性，所以这里用「值指纹 + 轻量轮询」兜底：
     每 200ms 只比一个字符串，代价可忽略，换来的是三条赋值路径都能可靠重取价。
     返回 commit 函数供调用方在需要时立即强制比对。 */
  function onCounterpartyChanged(input, callback) {
    if (!input || typeof callback !== 'function') return () => {};
    let last = String(input.value ?? '');
    const commit = () => {
      const current = String(input.value ?? '');
      if (current === last) return;
      last = current;
      callback(current);
    };
    input.addEventListener('change', commit);
    input.addEventListener('erp-counterparty-change', commit);
    window.setInterval(commit, 200);
    return commit;
  }

  /* 关联原单后对齐「往来对象 / 客户名称」（退货单、退拿货单共用一份）。
     关联都要求与原单同一对象（后端会硬校验），所以这里**以原单为准**：
     空则带出；已填且不同则改成原单对象并说明原因 —— 之前「已填就只警告不覆盖」会让用户
     带着一个注定被拒的对象去保存（F4，2026-10-14 用户实测反馈）。 */
  function syncCounterparty(source, nameInput, idInput) {
    if (!source || !nameInput) return '';
    const name = String(source.customer_name || '').trim();
    if (!name) return '';
    const id = source.customer_id === undefined || source.customer_id === null ? '' : String(source.customer_id);
    const current = String(nameInput.value || '').trim();
    if (current === name && (!idInput || String(idInput.value || '') === id)) return '';
    nameInput.value = name;
    if (idInput) idInput.value = id;
    return current && current !== name
      ? `已按原单把往来对象改为「${name}」（关联要求与原单同一对象）。`
      : `已带出原单往来对象：${name}。`;
  }

  /* 预览带入（F6，2026-10-14）：同一份预览只允许带入一次。

     原实现每次点击都「写 tbody.rows[0] + 追加 N-1 行」，点两次就插两套重复行
     （数量、金额、成本全翻倍）；而且无条件复用第 0 行会静默覆盖用户已手输的那一行。

     现在：第 0 行**只有是空行时才复用**，否则一律新增行；`apply()` 带入一次后
     拒绝再次带入（`applied()` 为真），调用方据此把按钮置灰；
     改选原单 / 清除关联时调用 `reset()` 重新放开（只换单才放开，重选同一张不放开）。

     F7（2026-10-14 用户实测）：带入还必须是**可替换**的，不能只追加。
     换原单时上一张原单带入的行必须一起撤掉，否则明细表会把两张单的行叠在一起
     —— 用户看到的就是「换了个原单，下面的行没消失，再点带入又多一套」。
     所以这里记下自己插过哪些行（`rows()`）：
       - `modifiedRows()`：这些行里已被手工改动过的（调用方据此决定要不要先确认，B 方案）；
       - `clear()`：撤掉这些行并放开按钮，返回被撤掉的行；**手输行一行不动**。
     `reset()` 只放开按钮、保留记录（用户拒绝移除时可以「保留旧行、再带入新单」）。 */
  function createPreviewApply({ tbody, addRow, isRowEmpty, fillRow, signatureOf }) {
    let applied = false;
    let inserted = [];
    const signature = tr => (typeof signatureOf === 'function' ? String(signatureOf(tr)) : '');
    const pickRow = index => {
      if (index === 0) {
        const first = tbody.rows[0];
        if (first && isRowEmpty(first)) return first;
      }
      return addRow();
    };
    return {
      applied: () => applied,
      rows: () => inserted.map(entry => entry.tr),
      modifiedRows: () => inserted.filter(entry => signature(entry.tr) !== entry.signature).map(entry => entry.tr),
      reset() { applied = false; },
      clear() {
        const removed = inserted.map(entry => entry.tr);
        removed.forEach(tr => tr.remove());
        inserted = [];
        applied = false;
        return removed;
      },
      apply(items) {
        if (applied || !items || !items.length) return false;
        items.forEach((item, index) => {
          const tr = pickRow(index);
          if (!tr) return;
          fillRow(tr, item);
          // 记下带入后的原始值：`modifiedRows()` 靠它区分「还是带入的那几行」与「用户改过的行」。
          inserted.push({ tr, signature: signature(tr) });
        });
        applied = true;
        return true;
      },
    };
  }


  // ---------------------------------------------------------------------------
  // 行复制 / 粘贴（2026-10-17 用户需求）
  //
  // 场景：同种商品不同型号连续开单（一物一档，DN50 与 DN65 是两条商品记录）。
  // 用户复制一行 → 粘贴到下一行 → 只改型号 → 该型号在该客户那里有专属价就自动带出。
  //
  // ★ 必须守住的最后一道线：粘贴过来的单价属于**上一行那个型号**，
  //   而现有规则是「手输单价 > 0 会被学成该客户对该商品的专属价」。
  //   如果用户改了型号却没重填单价，旧价就会被学进新型号，从此这家客户
  //   对新型号永远显示错价。所以本模块给粘贴行打 __pastedFrom 标记，
  //   一旦名称变了就**先把单价清空**再走正常的取价流程。
  // ---------------------------------------------------------------------------
  var copiedRow = null;   // {name, unit, quantity, price}
  var ROW_SELECTOR = 'tr:not(.slip-notice)';

  function cellValue(tr, selector) {
    var el = tr.querySelector(selector);
    return el ? String(el.value || '') : '';
  }

  function dataRows(tbody) {
    return Array.prototype.slice.call(tbody.querySelectorAll(ROW_SELECTOR));
  }

  function notify(tr, message) {
    var cell = tr.querySelector('.product-picker') || tr;
    var prev = cell.querySelector('.row-copy-toast');
    if (prev) prev.remove();
    var toast = document.createElement('span');
    toast.className = 'row-copy-toast';
    toast.setAttribute('role', 'status');
    toast.textContent = message;
    cell.appendChild(toast);
    setTimeout(function () { if (toast.parentNode) toast.remove(); }, 2200);
  }

  function clearCopiedMarks(tbody) {
    tbody.querySelectorAll('.row-tool-btn.is-copied').forEach(function (b) {
      b.classList.remove('is-copied');
    });
  }

  function bindRowCopyTools(tbody, options) {
    if (!tbody || tbody.__rowCopyBound) return;
    tbody.__rowCopyBound = true;
    var opts = options || {};
    var onAfterChange = opts.onAfterChange || function () {};
    var addRow = opts.addRow || function () { return null; };

    tbody.addEventListener('click', function (event) {
      var btn = event.target.closest('[data-row-action]');
      if (!btn) return;
      var tr = btn.closest('tr');
      if (!tr) return;
      event.preventDefault();

      if (btn.dataset.rowAction === 'copy') {
        // 空行不允许复制：否则「粘贴」会给出一个空操作，用户会以为按钮坏了。
        var name = cellValue(tr, '.picker-name-input').trim();
        var price = cellValue(tr, '.unit-price').trim();
        if (!name && !price) {
          notify(tr, '这一行还是空的，没有可复制的内容');
          return;
        }
        copiedRow = {
          name: cellValue(tr, '.picker-name-input'),
          unit: cellValue(tr, '.unit'),
          quantity: cellValue(tr, '.quantity'),
          price: price,
        };
        clearCopiedMarks(tbody);
        btn.classList.add('is-copied');
        notify(tr, '已复制：' + (name || '（无名称）'));
        return;
      }

      if (btn.dataset.rowAction === 'paste') {
        if (!copiedRow) {
          notify(tr, '还没有复制任何行（先点左边的复制）');
          return;
        }
        pasteRowInto(tr, onAfterChange);
      }
    });
  }

  // 2026-10-18 用户决策（第二次收窄范围）：**纯剪贴板**。
  // 之前这版会自动插入到下一行、末尾没行还会新建、粘完还全选名称 —— 用户明确说
  // 「就只是复制 粘贴就可以了」。现在：把复制下来的内容写回**触发粘贴的这一行**，
  // 不跳行、不建行、不抢焦点。
  // 唯一保留的保护：粘贴带过来的单价属于「复制来源的那个型号」，而手输单价 > 0
  // 会被学成该客户对该商品的专属价。所以打 __pastedFrom 标记，型号一改就先清价，
  // 防止把上一个型号的价格永久学进新型号。
  function pasteRowInto(tr, onAfterChange) {
    var source = copiedRow;
    if (!tr || !source) return;

    var nameInput = tr.querySelector('.picker-name-input');
    var unitInput = tr.querySelector('.unit');
    var qtyInput = tr.querySelector('.quantity');
    var priceInput = tr.querySelector('.unit-price');

    if (nameInput) nameInput.value = source.name || '';
    if (unitInput) unitInput.value = source.unit || '';
    if (qtyInput) qtyInput.value = source.quantity || '';
    if (priceInput) priceInput.value = source.price || '';

    // picker 的隐藏字段立即同步；product_id 清空，避免把来源行的商品 id 带过来。
    var pid = tr.querySelector('.product-id');
    if (pid) pid.value = '';
    var pname = tr.querySelector('.product-name');
    if (pname) pname.value = (source.name || '').trim();

    tr.__pastedFrom = (source.name || '').trim();
    tr.__pastedPrice = source.price || '';

    onAfterChange(tr);
  }

  // 名称变化时调用：若本行是粘贴来的、且名称已不是当初那个型号，先清掉旧价。
  // 返回 true 表示已清价（调用方不必再做别的处理）。
  function dropPastedPriceOnRename(tr) {
    if (!tr || !tr.__pastedFrom) return false;
    var nameInput = tr.querySelector('.picker-name-input');
    var current = nameInput ? nameInput.value.trim() : '';
    if (current === tr.__pastedFrom) return false;
    var priceInput = tr.querySelector('.unit-price');
    if (priceInput) priceInput.value = '';
    tr.__pastedFrom = null;
    tr.__pastedPrice = null;
    return true;
  }

  function resetCopiedRow() {
    copiedRow = null;
  }

  function hasCopiedRow() {
    return !!copiedRow;
  }

  window.ErpOrderEntry = {
    money: money,
    bindAutocomplete: bindAutocomplete,
    bindUnitCell: bindUnitCell,
    onCounterpartyChanged: onCounterpartyChanged,
    syncCounterparty: syncCounterparty,
    closeFloatingProductSuggestions: closeFloatingProductSuggestions,
    createPreviewApply: createPreviewApply,
    bindRowCopyTools: bindRowCopyTools,
    dropPastedPriceOnRename: dropPastedPriceOnRename,
    resetCopiedRow: resetCopiedRow,
    hasCopiedRow: hasCopiedRow,
  };
})();
