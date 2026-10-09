/* Shared two-level product picker (产品名称 → 型号) for all order-entry pages. */
(function () {
  'use strict';

  function option(text, value) {
    var o = document.createElement('option');
    o.textContent = text;
    o.value = value == null ? '' : String(value);
    return o;
  }

  function groupByName(catalog) {
    var map = new Map();
    (catalog || []).forEach(function (p) {
      if (!map.has(p.name)) map.set(p.name, []);
      map.get(p.name).push(p);
    });
    return map;
  }

  // row: a <tr>/container holding .picker-name and .picker-spec selects.
  // catalog: [{id, name, spec, unit, default_price_cents}]
  // onPick: called with the chosen product object, or null when nothing valid is selected.
  function mountRow(row, catalog, onPick) {
    var nameSel = row.querySelector('.picker-name');
    var specSel = row.querySelector('.picker-spec');
    if (!nameSel || !specSel) return null;
    var byName = groupByName(catalog);

    function fillNames() {
      nameSel.replaceChildren(option('选择产品名称', ''));
      Array.from(byName.keys())
        .sort(function (a, b) { return a.localeCompare(b, 'zh-Hans-CN'); })
        .forEach(function (name) { nameSel.appendChild(option(name, name)); });
    }

    function specLabel(product) {
      var label = product.spec || '未填写型号';
      if (product.inventory_enabled === false) label += ' · 未启用，仅可保存草稿';
      return label;
    }

    function fillSpecs(name, selectedId) {
      var list = byName.get(name) || [];
      specSel.replaceChildren();
      specSel.appendChild(option(list.length > 1 ? '选择型号' : '型号', ''));
      list.forEach(function (p) { specSel.appendChild(option(specLabel(p), p.id)); });
      if (selectedId !== undefined && selectedId !== null && String(selectedId) !== '') {
        specSel.value = String(selectedId);
      } else if (list.length === 1) {
        specSel.value = String(list[0].id);
      }
    }

    function emit() {
      var picked = null;
      for (var i = 0; i < (catalog || []).length; i += 1) {
        if (String(catalog[i].id) === specSel.value) { picked = catalog[i]; break; }
      }
      onPick(picked);
    }

    nameSel.addEventListener('change', function () {
      fillSpecs(nameSel.value, '');
      emit();
    });
    specSel.addEventListener('change', emit);

    var api = {
      reset: function () {
        fillNames();
        nameSel.value = '';
        fillSpecs('', '');
        specSel.value = '';
      },
      set: function (product) {
        if (!product || product.id === undefined || product.id === null || product.id === '') {
          api.reset();
          return;
        }
        if (!byName.has(product.name)) {
          byName.set(product.name, [product]);
          fillNames();
        }
        nameSel.value = product.name;
        fillSpecs(product.name, product.id);
        specSel.value = String(product.id);
      },
    };
    api.reset();
    return api;
  }

  // G 段二（2026-10-09）：混合控件 —— 名称/型号可直接手输，命中已有商品则绑定，
  // 未命中则留纯文本由后端自动建档。仅供 orders/new.html（销售/退货）使用；
  // 拿货两页继续用上面的两级下拉（mountRow），互不影响。
  // row 需含：.picker-name-input .picker-spec-input .product-id .product-name .product-spec
  function mountHybrid(row, catalog, onPick) {
    var nameInput = row.querySelector('.picker-name-input');
    var specInput = row.querySelector('.picker-spec-input');
    if (!nameInput || !specInput) return null;
    var productIdField = row.querySelector('.product-id');
    var productNameField = row.querySelector('.product-name');
    var productSpecField = row.querySelector('.product-spec');

    function norm(s) { return String(s == null ? '' : s).trim(); }

    function findMatch(name, spec) {
      var n = norm(name), sp = norm(spec);
      if (!n) return null;
      for (var i = 0; i < (catalog || []).length; i += 1) {
        var p = catalog[i];
        if (norm(p.name) === n && norm(p.spec) === sp) return p;
      }
      return null;
    }

    function nameSuggestions() {
      var seen = new Set();
      (catalog || []).forEach(function (p) { seen.add(p.name); });
      return Array.from(seen);
    }

    function specSuggestions(name) {
      var n = norm(name);
      var seen = new Set();
      (catalog || []).forEach(function (p) { if (norm(p.name) === n) seen.add(p.spec || ''); });
      return Array.from(seen);
    }

    function syncHidden() {
      productNameField.value = norm(nameInput.value);
      productSpecField.value = norm(specInput.value);
    }

    function refresh() {
      syncHidden();
      var match = findMatch(nameInput.value, specInput.value);
      if (match) {
        productIdField.value = match.id;
      } else {
        // 未命中：清空 id，交给后端按「名称+型号」自动建档（零建档路径）。
        productIdField.value = '';
      }
      onPick(match);
    }

    nameInput.addEventListener('input', refresh);
    specInput.addEventListener('input', refresh);
    nameInput.addEventListener('change', refresh);
    specInput.addEventListener('change', refresh);

    var api = {
      reset: function () {
        nameInput.value = '';
        specInput.value = '';
        productIdField.value = '';
        syncHidden();
      },
      set: function (product) {
        if (!product || product.id === undefined || product.id === null || product.id === '') {
          api.reset();
          return;
        }
        nameInput.value = product.name || '';
        specInput.value = product.spec || '';
        productIdField.value = product.id;
        syncHidden();
      },
      names: nameSuggestions,
      specs: specSuggestions,
    };
    // 初次渲染：已有 id 说明是重编辑，回填名称/型号。
    if (productIdField.value) {
      var existing = null;
      for (var i = 0; i < (catalog || []).length; i += 1) {
        if (String(catalog[i].id) === String(productIdField.value)) { existing = catalog[i]; break; }
      }
      if (existing) api.set(existing);
    }
    syncHidden();
    return api;
  }

  window.ErpProductPicker = { mountRow: mountRow, mountHybrid: mountHybrid };
})();
