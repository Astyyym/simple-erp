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

  window.ErpProductPicker = { mountRow: mountRow };
})();
