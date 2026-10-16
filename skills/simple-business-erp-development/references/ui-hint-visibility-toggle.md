# 界面说明文字开关（data-ui-hint）

2026-10-16 新增：设置页「显示」→「界面说明文字」，关掉后隐藏各页面的提示/说明/注意类小字。
用户口径：界面上的灰色说明小字（设置页 / 开单页 / 列表页的「提示、说明、注意」段落）由一个开关统一管理。

## 机制（三处，缺一不可）

- 配置项 `ui_show_hints`：`config.py` 的 `CONFIG_DEFAULTS`，默认 `True`。设置页表单是复选框，
  **未勾选时浏览器不提交该字段**，所以 `routes/settings.py::save_settings` 按「在场即开、缺席即关」解析
  —— 不能照抄 `print_offset_*` 那种「只在字段在场时更新」的写法，否则永远关不掉。
- `erp/__init__.py` 的 context processor 下发 `ui_show_hints`；`base.html` 的 `<html>` 输出
  `data-ui-hints="on|off"`；一条 CSS 收口全部隐藏：
  `html[data-ui-hints="off"] [data-ui-hint]{display:none!important}`。
- 受控元素在模板里**逐处显式**写 `data-ui-hint`（当前约 75 处）。不要用 `.text-muted.small`、`.help`
  这类 class 选择器一刀切：同一批 class 里混着数据单元格（导入原因 `import_preview.html`、
  更新时间戳 `products/edit.html`）和状态文字，一刀切会误伤。

## 不纳入受控（无论开关都必须可见）

- 危险操作警告：「此操作不可撤销」「恢复旧版备份会自动升级到 v15…」等 `text-danger` 文案。
- 状态与数据：当前状态文字、计数、金额、日期值、`settings-dirty-hint`。
- 空态提示：「今天还没有正式保存或已打印的单据」「还没有客户资料。」
- `placeholder` 与输入提示、按钮文案、弹窗交互正文（如「离开前要先保存吗？」）。

## 改这块时的检查

- 测试：`tests/test_ui_hints_toggle.py`（默认开启、CSS 规则存在、开关往返、危险警告不带标记、数据单元格不带标记）。
- 给说明段加 `data-ui-hint` 后，**不要再用 `<p class="x">` 这种严格前缀写断言**：属性落在 class 之前
  （`<p data-ui-hint class="x">`），老正则直接失配（已按此修正 `tests/test_master_data_list_layout.py`
  的 `_legend`）。
- 新增说明文字时同步打标记，否则开关会「漏字」；新增危险警告时**不要**打标记。
