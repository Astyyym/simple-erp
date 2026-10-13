/* Shared order-entry helpers for all four entry pages (and, from Phase 3, the list pages).
   Single source of truth: the sales page used to carry an inline copy of these functions. */
(function () {
  'use strict';

  function money(n) {
    return (Math.round((Number(n) || 0) * 100) / 100).toFixed(2);
  }

  // Format the six-decimal cost payload without binary-float rounding.
  function costMoney(costText) {
    const negative = String(costText).startsWith('-');
    const [whole, fraction = ''] = String(costText).replace(/^-/, '').split('.');
    const micro = BigInt(whole) * 1000000n + BigInt((fraction + '000000').slice(0, 6));
    const cents = (micro + 5000n) / 10000n;
    return (negative && cents !== 0n ? '-' : '') + String(cents / 100n) + '.' + String(cents % 100n).padStart(2, '0');
  }

  function closeSuggestions(input, box) {
    if (box.hasAttribute('popover') && box.matches(':popover-open')) box.hidePopover();
    box.classList.add('d-none');
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    box.querySelectorAll('[role="option"]').forEach(option => option.setAttribute('aria-selected', 'false'));
  }

  function showSuggestions(input, box, items, render, onPick, emptyText = '无匹配项') {
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
      box.style.maxHeight = `${Math.min(220, upwards ? above : below)}px`;
      if (!box.matches(':popover-open')) box.showPopover();
      box.style.left = `${Math.max(12, Math.min(anchor.left, window.innerWidth - box.offsetWidth - 12))}px`;
      box.style.top = `${upwards ? anchor.top - box.offsetHeight - 4 : anchor.bottom + 4}px`;
    }
    input.setAttribute('aria-expanded', 'true');
    input.removeAttribute('aria-activedescendant');
  }

  // Generic combobox: loadItems(query) -> array; renderOption(item) -> node; onPick(item).
  // emptyText: 无匹配时的文案（零建档路径的页面应给出「保存时会新建」而不是断言性的「无匹配项」）。
  function bindAutocomplete(input, box, loadItems, renderOption, onPick, onInputChange = () => {}, emptyText = '无匹配项') {
    let matches = [];
    let activeIndex = -1;
    let requestSequence = 0;
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
    const choose = item => { onPick(item); close(); };
    const refresh = async (query = input.value.trim(), initialDirection = '') => {
      const sequence = ++requestSequence;
      try {
        const result = await loadItems(query);
        if (sequence !== requestSequence) return;
        matches = Array.isArray(result) ? result : [];
        showSuggestions(input, box, matches, renderOption, choose, emptyText);
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
      if (!query) { requestSequence += 1; matches = []; close(); return; }
      refresh(query);
    });
    input.addEventListener('compositionstart', () => { composing = true; });
    input.addEventListener('compositionend', () => {
      composing = false;
      onInputChange();
      const query = input.value.trim();
      if (!query) { requestSequence += 1; matches = []; close(); return; }
      refresh(query);
    });
    input.addEventListener('focus', () => {
      if (input.value.trim()) refresh(input.value.trim());
    });
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

  window.ErpOrderEntry = {
    money: money,
    costMoney: costMoney,
    bindAutocomplete: bindAutocomplete,
    closeFloatingProductSuggestions: closeFloatingProductSuggestions,
  };
})();
