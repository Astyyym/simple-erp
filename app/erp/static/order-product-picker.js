/* Shared product picker for all order-entry pages.
   v14（2026-10-13）：商品身份只剩「名称」——名称与型号合并为一个字段，因此
   两种挂载方式都变成单框：
     mountRow   —— 单一下拉（拿货 / 退拿货：必须选定已建档商品，取值是 product id）
     mountHybrid —— 单行输入框 + 建议（销售 / 退货：可手输，未命中交给后端自动建档）*/
(function () {
  'use strict';

  function option(text, value) {
    var o = document.createElement('option');
    o.textContent = text;
    o.value = value == null ? '' : String(value);
    return o;
  }

  function norm(s) { return String(s == null ? '' : s).trim(); }

  // row: a <tr>/container holding one .picker-name select.
  // catalog: [{id, name, unit, inventory_enabled}]（2026-10-15 起目录不再带售价字段）
  // onPick: the chosen product object, or null when the placeholder is selected.
  function mountRow(row, catalog, onPick) {
    var nameSel = row.querySelector('.picker-name');
    if (!nameSel) return null;

    function sortedCatalog() {
      return (catalog || []).slice().sort(function (a, b) {
        return String(a.name).localeCompare(String(b.name), 'zh-Hans-CN') || (a.id - b.id);
      });
    }

    function label(product) {
      var text = product.name;
      if (product.inventory_enabled === false) text += ' · 未启用，仅可保存草稿';
      return text;
    }

    function fill(selectedId) {
      nameSel.replaceChildren(option('选择产品名称', ''));
      sortedCatalog().forEach(function (p) { nameSel.appendChild(option(label(p), p.id)); });
      if (selectedId !== undefined && selectedId !== null && String(selectedId) !== '') {
        nameSel.value = String(selectedId);
      }
    }

    function emit() {
      var picked = null;
      for (var i = 0; i < (catalog || []).length; i += 1) {
        if (String(catalog[i].id) === nameSel.value) { picked = catalog[i]; break; }
      }
      onPick(picked);
    }

    nameSel.addEventListener('change', emit);

    var api = {
      reset: function () {
        fill('');
        nameSel.value = '';
      },
      set: function (product) {
        if (!product || product.id === undefined || product.id === null || product.id === '') {
          api.reset();
          return;
        }
        fill(product.id);
        nameSel.value = String(product.id);
      },
    };
    api.reset();
    return api;
  }

  // 销售 / 退货：单框手输 + 名称建议；命中已有商品则绑定 id，未命中留纯文本交给
  // 后端按名称自动建档（零建档路径）。
  // row 需含：.picker-name-input .product-id .product-name
  // options.requireExisting: true —— 拿货 / 退拿货：必须命中已建档商品，未命中不写 id
  //   （由 onMissing 提示，后端也会拒单），不交给后端自动建档。
  function mountHybrid(row, catalog, onPick, options) {
    var nameInput = row.querySelector('.picker-name-input');
    if (!nameInput) return null;
    var requireExisting = !!(options && options.requireExisting);
    var onMissing = (options && options.onMissing) || function () {};
    var productIdField = row.querySelector('.product-id');
    var productNameField = row.querySelector('.product-name');

    function findMatch(name) {
      var target = norm(name);
      if (!target) return null;
      for (var i = 0; i < (catalog || []).length; i += 1) {
        if (norm(catalog[i].name) === target) return catalog[i];
      }
      return null;
    }

    function nameSuggestions() {
      var seen = new Set();
      (catalog || []).forEach(function (p) { seen.add(p.name); });
      return Array.from(seen);
    }

    function syncHidden() {
      productNameField.value = norm(nameInput.value);
    }

    function refresh() {
      syncHidden();
      var match = findMatch(nameInput.value);
      var typed = norm(nameInput.value);
      // 未命中：销售 / 退货交给后端按名称自动建档（零建档路径）；
      // 拿货 / 退拿货（requireExisting）不写 id —— 由页面提示，后端也会拒单。
      productIdField.value = match ? match.id : '';
      if (!match && requireExisting && typed) onMissing(typed);
      onPick(match);
    }

    nameInput.addEventListener('input', refresh);
    nameInput.addEventListener('change', refresh);

    var api = {
      reset: function () {
        nameInput.value = '';
        productIdField.value = '';
        syncHidden();
      },
      set: function (product) {
        if (!product || product.id === undefined || product.id === null || product.id === '') {
          api.reset();
          return;
        }
        nameInput.value = product.name || '';
        productIdField.value = product.id;
        syncHidden();
      },
      names: nameSuggestions,
    };
    // 初次渲染：已有 id 说明是重编辑，回填名称。
    if (productIdField.value) {
      for (var i = 0; i < (catalog || []).length; i += 1) {
        if (String(catalog[i].id) === String(productIdField.value)) { api.set(catalog[i]); break; }
      }
    }
    syncHidden();
    return api;
  }

  window.ErpProductPicker = { mountRow: mountRow, mountHybrid: mountHybrid };
})();
