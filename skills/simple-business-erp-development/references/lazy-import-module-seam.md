# 惰性导入重依赖时的模块级 seam 规则

用途：把 `weasyprint` / `openpyxl` / `pypinyin` / `PIL` 等重依赖从模块顶层 import 改成「首次使用时导入」以加快启动时，**必须保留测试所依赖的模块级属性 seam**。写错会让测试出现隐蔽的假阳性。

## 两条铁律

1. **不能写成函数内裸 `from x import y`**：模块上就没有 `HTML` 等属性，`monkeypatch.setattr(module, "HTML", ...)` 直接 `AttributeError`。会连带挂 9–10 个测试。
2. **测试"怎么用"这个 seam 决定写法**，分两张面孔：

### 面孔 A：测试只 `setattr`，从不读取 → 用模块级 `None` 声明 + 解析器

适用：`erp/utils/pdf.py`（`HTML`）、`exporting.py`（`Workbook`）、`importing.py`（`Workbook`/`load_workbook`）、`routes/products.py`（`Image`/`UnidentifiedImageError`）、`utils/pinyin.py`（`lazy_pinyin`/`Style`）。

```python
HTML = None

def _html_class():
    global HTML
    if HTML is None:
        from weasyprint import HTML as _HTML
        HTML = _HTML
    return HTML

# 调用点
_html_class()(string=html, base_url=...).write_pdf(out)
```

### 面孔 B：测试会**读取**模块属性当真实类用 → 必须用 PEP 562 模块 `__getattr__`

适用：`erp/utils/purchase_pdf.py`（`CSS`/`HTML`）。因为 `tests/test_purchase_printing.py` 里有
`original_html = purchase_pdf.HTML`，把真实类取出来再包成 spy / `LayoutAndRealPdf`。

```python
def __getattr__(name):
    if name in ("CSS", "HTML"):
        from weasyprint import CSS as _CSS, HTML as _HTML
        return {"CSS": _CSS, "HTML": _HTML}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _weasy():
    global CSS, HTML
    globals_css = globals().get("CSS")
    globals_html = globals().get("HTML")
    if globals_css is None:
        from weasyprint import CSS as _CSS
        CSS = globals_css = _CSS
    if globals_html is None:
        from weasyprint import HTML as _HTML
        HTML = globals_html = _HTML
    return globals_css, globals_html
```

## 最隐蔽的坑：测试顺序假阳性

用面孔 A 的 `None` 声明套在 `purchase_pdf.py` 上时：

- **整文件一起跑**：同文件靠前的测试先渲染过一次，把 `HTML` 解析成真实类 → 后面读 `purchase_pdf.HTML` 拿到真实类 → `34 passed`，**看起来全绿**。
- **单独跑那 3 个测试**：没有任何前置渲染 → `original_html = purchase_pdf.HTML` 拿到 `None` → `TypeError: 'NoneType' object is not callable` → `3 failed`。

**结论：验收惰性导入改动时，"单独跑"是必做的一层，不能只看整文件或全量绿。**

## 验收清单（改惰性导入后必做）

1. 全量 `pytest tests/ -q` 不低于基线、不新增 failed。
2. **启动纯度**：新进程 `import erp` 后 `weasyprint`/`openpyxl`/`PIL`/`pypinyin` **都不在** `sys.modules`。
3. **隔离单跑**：`purchase_pdf.py` 相关的 `test_purchase_calibration_and_ui_zoom_do_not_change_pdf_geometry[False/True]` 与 `test_purchase_real_pdf_keeps_twelve_mm_margins_on_every_fragment` 必须单独跑也绿。
4. 功能冒烟：拼音建档、Excel 导出、Excel 模板、图片上传、销售单 PDF 各跑通一次（惰性解析器首次触发要真的能用）。
5. **不改** `简单ERP.spec` 的 `hiddenimports`（`weasyprint`/`pydyf`/`pypinyin` 必须继续列出，函数内导入仍需它）。

## 关于 skip 数波动

`tests/test_purchase_printing.py`（`shutil.which('pdftotext')`）与 `tests/test_pdf_pan_preview_js.py`（`shutil.which('node')`）会因 PATH 里找不到可执行文件而 skip。**PATH 被裁剪的进程（如被自动转成后台进程）会产生 skip，前台全 PATH 则实际执行并通过**——收集总数不变，这不是回归。比较测试结果时先确认两次跑的 PATH 一致。
