# 简单ERP 更新记录

> 各版本条目按当时发布口径保留。v0.8.0 永久免登录已经取代早期版本中记录的本机登录门；历史凭据不在更新记录中保存。

## v4.6.3 - 空开单页误弹「未保存」+ 打印抬头换位 + 首次使用引导弹窗（2026-10-21，未发版）

> 本版尚未打 tag、未打包、未推送远端；下方「验证」一节只包含已实际执行的测试与真机探针结果。

- **用户报的现象（4.6.2 反馈）**：开单页里**没有任何东西**，切换到别的页面却弹出「未保存」提醒，点「离开」后本页信息被清空；**重启应用后该现象消失**。
- **根因一：脏标记只置位、永不复位**。四类开单页都是 `let formDirty = false; form.addEventListener('input', () => formDirty = true)` —— 只要脏过一次就再也洗不掉。用户把内容全删干净、点「一键清空」清空后，页面明明是空的，切页照样拦人，并诱导用户确认离开而**真的丢掉内容**。重启之所以「好了」，只是换了一份干净的页面内存，并非修复了什么。
- **根因二：单位候选「×」派发合成事件**。`order-entry-common.js` 的 `bindUnitCell` 在清除单位时执行 `input.dispatchEvent(new Event('input', {bubbles:true}))`，这个合成事件冒泡到表单，把脏标记打上。**用户一个字都没输入、只点了一下叉号**，页面就被判成已修改。
- **修法：把「是否脏」从布尔开关改成实时快照比对**。新增 `ErpOrderEntry.createDirtyTracker(form)`：记下表单打开时的值指纹（按 DOM 顺序收集具名控件的 `name=value`），任何时刻只比「当前指纹 vs 初始指纹」。
  - 值回到初始状态（删空、一键清空、新增行又删掉）→ **自动不算脏**，不需要每个入口各自记得复位，也就不存在「漏掉某个入口」这类再犯空间。
  - 合成事件天然免疫：它把「空」写成「空」，指纹没变。
  - 不带 `name` 的控件（换页提示行、行内按钮组）不参与指纹。
  - 保存成功后 `markSaved()`，不再拦人。
- **覆盖面**：共替换 **6 处 `beforeunload`** —— 三份录入模板各自的新建模式与编辑模式（`orders/new.html` 同时承载销售单与退货单，`purchases/new.html` 承载拿货单，`purchases/return_new.html` 承载退拿货单）。旧的 `formDirty` / `editDirty` 变量已全部删除，模板内不再残留。
- **打印单抬头换位（四类单据共用模板）**：`orders/print_template.html` 的 `.meta` 由「单号 / 日期 / 客户」改为 **「客户 / 日期 / 单号」**——客户贴左、日期居中、单号贴右。拿货 / 退拿货的长标签「往来对象（本单卖方）」原样保留，只换位置。`.meta` 用 `table-cell` + `nth-child` 做三格对齐，DOM 顺序即视觉位置，故无需改样式。
- **首次使用引导弹窗**（取代工作台原先那张「打印单上还有空白」提示卡，避免同一件事提示两次）：
  - **触发口径**（`config.first_run_prompt_due`）：公司名称仍是出厂默认「简单ERP」**且**用户没点过「不再提示」**且**不在「稍后再说」的 7 天冷却期内。之所以按「是否等于默认值」判定而不是「字段为空」，是因为 `shop_name` 有出厂值，判空永远不成立——老用户升级后一次都看不到引导，打印单上会一直印着「简单ERP」。
  - **三个字段**：公司名称 + 订货电话 + 订货地址；**三个出口**：保存并开始使用 / 稍后再说（7 天后，`first_run_prompt_snoozed_until` 存 ISO 日期，比时间戳好读且不受时区漂移影响）/ 不再提示。
  - **不复用 `/settings/save`**：那个路由把表单里缺失的字段一律写成空串（`print_main_business`、`print_legal_note`、`ui_theme`、`ui_scale` …），而弹窗只提交三个字段 —— 走通用保存会把主营业务、法律句、主题、缩放**全部清空**。故新增专用路由 `POST /settings/first-run/save`，只写自己负责的三个键。
  - 新增配置键 `first_run_prompt_snoozed_until` / `first_run_prompt_dismissed`，并同步 `packaging/default_config.json`（`test_config_fallback.py::test_factory_template_matches_every_code_default` 会逐键比对，漏同步即失败）。
- **明确不做**：商品名候选框加「×」删除商品档案 —— 用户评估后撤回。商品名唯一索引 `idx_products_business_identity ON products(TRIM(name))` **把软删除行也算在内**，软删后同名商品永久占位；且保存时 `accounting.py` 校验 `deleted_at IS NULL`，删掉正在填的那行商品会让整张单保存失败（报「商品不存在或已删除」）。要做只能走彻底删除 `purge_product_item`，代价与影响面超出本次范围。
- **验证（已执行）**：
  - 全量 `pytest tests/ -q` = **1057 passed / 2 skipped / 0 failed**。
  - 新增 `tests/frontend/order_dirty_tracker.test.mjs`（7 项，锁住：值变化判脏 / **删空不再判脏** / **合成事件不判脏** / 保存后不拦 / reset 换基准 / 增删行 / 无名控件不参与），`node --test` = 7 passed。
  - 新增 `tests/test_print_meta_order.py`（5 项，四类单据抬头顺序 + 销售退货默认标签回落「客户」）。
  - 重写 `tests/test_print_info_prompt.py`（14 项）：从旧提示卡行为改为弹窗行为，并**反向断言旧卡 `printInfoNotice` 必须彻底消失**。
  - **真实 WebView2（Edge 155，pywebview 6.2.1 + 远程调试端口）实测 6 个场景**：输入后删空 `true→false`；点单位「×」`false`；一键清空后切页 `false` 且无弹框；**有内容时切页仍弹框**（未改坏）；新增行再删除 `true→false`；行内清空但客户名仍在 → 仍判脏（本该如此）。
  - 弹窗链路实测：初始弹 → 「稍后再说」不弹且冷却到 `2026-10-28` → 冷却改过期后重新弹 → 保存后不弹且 `shop_name`/电话/地址落库，同时**主营业务、法律句、`ui_theme`、`ui_scale` 均未被清空**。
- **仍未做**：打 tag、打包、推送远端、发版证据回填；桌面壳内的截图验收（本次改动未触及打印版式几何，只改抬头三格顺序）。

## v4.6.2 - 带括号/空格的商品点选后专属价带不出来（2026-10-20）

- **用户报的现象**：某个商品在该客户处**确实有专属价**，模糊匹配的下拉框也能搜到它，但点选之后**只有商品名称进了单元格，单价一直空白**。用户自己也说不清是偶发还是必现。
- **根因：同一个名称，两侧形态不一致**。点选后前端会拿**完整名称**回查一次价格（`refineProductRow()`），而后端 `normalize_name_query()` 会先做 NFKC 归一再去掉空格 —— `PVC管（厚）` 变成 `PVC管(厚)`；可库里 `name` 列存的仍是**全角括号原样**。于是 `name LIKE '%PVC管(厚)%'` 永远打不中，接口返回空数组，单价就停在被清空后的空白值上。
  —— 名称本身是同步写入的（`picker.set()`），所以「名称进得去、价格进不来」正是这个 bug 的指纹。
- **为什么时好时坏**：只和品名形态有关。含**全角括号、全角数字/字母、空格、撇号**的名称必现；纯半角紧凑名称从来不出问题。所以同一家店里一部分商品一直坏、另一部分一直好。
- **修复（消掉形态不一致，而不是加兜底）**：
  - 新增 `name_norm` 派生列（商品 + 客户），写入时按**与检索串同一套规则**归一存一份，匹配改为比较这一列。原 `name` 列原样保留，打印与展示口径不变。
  - `pinyin_full` / `pinyin_initials` 改为基于 **NFKC + 去括号**后的名称生成：`PVC管（厚）` 由 `pvcguan（hou）` 改为 `pvcguanhou`，全拼搜索不再被全角括号挡住。
  - 老库升级时 `backfill_name_norm()` 自动回填 `name_norm`，并把**旧规则生成的错误拼音列一起重算**（只动「必然带旧产物」的行，人工维护过的非空值不被覆盖）。
  - **为什么不在 SQL 里现场归一**：实测 20000 行从 1ms 涨到 109ms（慢 81 倍）且索引失效，故改为存列，查询开销回到原样。
- **顺带修好的**：此前「首字母能搜、全拼搜不到」在带括号/全角的品名上同样存在（`pvcguanhou` 打不中），本版一并解决；全项目 20 处名称检索点（开单/单据筛选/商品档案/客户/拿货/退货源/手机端/PDF）共用 `name_match_clause()`，改一处全部生效。
- **四类开单范围确认**：只有**销售单 / 退货单**按客户专属价自动带价，本版修复覆盖这两条（含「换客户后重算所有行」那条路径）；**拿货 / 退拿货**的单价一直是手输的单据金额，不接专属价，本版不动。同时改正两处错误注释——原注释把拿货单价写成「入库成本价」，实际库存系统已删除、无入库动作，单价只参与该单据汇总。
- **手机端同步**：手机端路由无一行自写 SQL，全部走 `readonly_queries` 的同一套匹配函数，因此自动同步；已用真实手机端 app + 真实配对流程实测客户/商品全拼搜索与全角完整名搜索均命中。第二监听不调 `init_db()`（避免重复迁移），回填由主应用先完成，两端共用同一数据根。
- **回归**：新增 `tests/test_name_norm_search.py`（29 项，覆盖 `PVC管（厚）`、`水龙头（铜）`、`软密封闸阀 DN100`、`４分弯头`、`电缆ＹＪＶ２２`、`螺栓 M8×40` 六类形态的点选回查取价、换客户重算、全拼搜索、老库回填）与 `tests/test_name_norm_mobile.py`（5 项，手机端配对后搜索）。
- **验证**：全量 `pytest tests/ -q` = **1053 passed / 1 skipped / 0 failed**；前端 `node --test tests/frontend/*.test.mjs` = **103 passed / 0 failed**。
- **打包（2026-10-20，`打包Windows安装包.bat` 一次出齐）**：`dist/simple-erp-setup-v4.6.2.exe` 41,400,125 B（SHA256 `6865711312…`）、`dist/simple-erp-windows-v4.6.2.zip` 55,269,415 B（`2c5abea96a…`）、`dist/local-v4.6.2/简单ERP/`。**产物门**：ZIP 623 条目、顶层唯一 `简单ERP/`、无 `.db` / `demo_data` / 日志、包内 `VERSION` = `v4.6.2`；两个 EXE 的 PE 版本均为 `4.6.2.0` / `v4.6.2`；绿色版演示数据 **200 条**（`orders` 125 = 销售 90 + 退货 35，`purchase_orders` 45，`purchase_return_orders` 30，`integrity_check`=ok）。
- **隔离冒烟（分发产物，非源码）**：直接启动 `dist/local-v4.6.2/简单ERP/简单ERP.exe`（`ERP_PORT=5098`），`/health` = `v4.6.2`。冒烟库内插入一条带全角括号的商品 `PVC管（厚）冒烟`（客户专属价 88.88）后实测：**按完整名称（全角括号）回查拿到 `88.88`**、全拼 `pvcguanhoumaoyan` 同样命中、换成无专属价的客户则 `unit_price` 正确为 `null` —— 用户报的那个现象在**打包产物**上确认修复。
- **发版证据（2026-10-20）**：2 个提交推 `main`（`fe773e7..24b013a`，`git ls-remote origin refs/heads/main` = `24b013a9` 与本地 HEAD 一致）；annotated tag `v4.6.2`（tag 对象 `0db7ce0c` 解引用回 `24b013a`）；[GitHub Release v4.6.2](https://github.com/Astyyym/simple-erp/releases/tag/v4.6.2)（draft=false、prerelease=false、target=`main`）挂三个资产：`simple-erp-setup-v4.6.2.exe`（41,400,125 B，SHA256 `68657113…`）、`simple-erp-windows-v4.6.2.zip`（55,269,415 B，`2c5abea9…`）、`simple-erp-mobile-v1.2.0.apk`（43,608 B，`4a7e3c50…`；本版未改手机壳，沿用 1.2.0）—— Release API 的 `digest` 与本地 SHA256 一致，`gh release download` 取回后与本地产物 `cmp` **三个全部字节一致**。旧 v4.6.0 / v4.6.1 三产物与 `local-*` 目录已清理，`dist/` 只留 v4.6.2。
- **仍未做**：目标打印机实打本版不需要（不动打印版式）；桌面壳 WebView2 内的点击链路未在本次改动范围内。

## v4.6.1 - 开单行按钮位置调整 + 单位候选清除 + 手机端交互收口（2026-10-18，已发版）

- **手机端交互收口（2026-10-18 真机尺寸逐页实测后修）**：
  - **触控目标守住 44px**：单据页类型 chip、客户/商品页排序 chip、设置页主题三选从 38px 提到 44px；客户详情「展开全部 N 笔流水」从 **26px** 提到 44px（只扩热区，字号与颜色一字未动）。
  - **单据列表行重排**：主行改为「客户名 …… 金额」，单号退到上方小字行。上一版一度把金额挪到客户名那一行，导致右列只剩一个孤立小标签、按自身高度居中浮在单号行的高度上，比客户名和金额高 10px、右边缘还偏左 59px —— 现恢复成 **标签在上、金额在下** 的同一纵向组，两者右边缘同贴 325px；大金额按位数分档缩字号（≤5 位 14px / 6 位 13px / ≥7 位 12px），`¥17,188.66` 不截断、不换行、不挤掉客户名。
  - **客户/商品行副标题** 12 → 11.5px、行高 1.4，320px 窄屏上「拼音 · 手机号」不再被截成 `andyfiresupply · 132…`。
  - **静态资源内容指纹**：主应用与第二监听共用 `static_url()`，地址带 sha1 指纹 + 一年缓存，改一个字节地址就变 —— 手机上不会吃到旧 CSS/JS。
- **安卓壳升到 `1.2.0`（versionCode 3）**：新增 `applyDarkMode` 原生桥，页面切换浅/深色时回传壳层，重设 window / 状态栏 / 导航栏底色与图标明暗（此前只在 `onCreate` 读一次系统 uiMode，用户在页面里切深色后状态栏永远不变）；设置页不再把提示文字当值填进输入框。
- **验证**：全量 `pytest` **1019 passed / 1 skipped**；前端 `node --test` **103 passed**；真实 Chrome（CDP，390×844 / 320×640 / 844×390 横屏）逐页实点，量到标签 170–192、金额 195–213 竖成一组、右边缘均 325。真机/模拟器侧：`v4.6.1` 覆盖安装后 `config.json` 与业务库指纹逐字节不变，深色下整页（含顶部状态栏区域）一致，配对成功后 cookie 落盘 71 字节。

- **复制 / 粘贴按钮移到产品名称单元格最左侧**（四类开单：销售 / 退货 / 拿货 / 退拿货）：按钮组从输入框右缘搬到 `.picker-row` 首位，按钮组与商品名称输入框之间加一道竖线分隔符（`.row-copy-sep`），与右侧文字拉开距离。不新增表格列、不加宽操作列；窄屏 300px 内仍不挤坏输入框。事件委托仍挂在 tbody 上，复制/粘贴的剪贴板语义与「换型号先清旧价」的保护一行未改。
- **单位联想下拉每个候选右侧新增「×」**：点击只清空当前行的单位输入值（并保持焦点留在输入框、候选继续可用），**不动候选池** —— 候选来自库内历史单位，不该被一次点击删掉。叉号拦下 mousedown / click 冒泡，避免点它时连带选中该候选并关闭下拉。
- **实现细节**：`bindUnitCell` 的渲染函数改为返回 `DocumentFragment`（`showSuggestions` 只 `appendChild` 一个节点，返回包裹节点会丢掉叉号）；四页 `order-entry-common.js` 的 `?v=` 统一升到 `v=4`，防桌面壳缓存旧 JS。
- **验证**：`pytest tests/test_unit_entry_pages.py tests/test_async_save_print.py` 9 passed；前端 `node --test tests/frontend/*.test.mjs` **103 passed / 0 failed**；真实 Chrome（CDP，独立 user-data-dir）四页逐页截图确认按钮在最左、竖线可见、下拉叉号渲染正确，并实测选中「个」后点叉号输入框变为空。

## v4.6.0 - 开单行复制粘贴 + 设置页收口（2026-10-18，已发版）

- **四类开单每行的产品名称右侧新增「复制 / 粘贴」两个常显 icon 按钮**（销售/退货/拿货/退拿货，内嵌在 `.picker-row` 内、不新增表格列、不加宽操作列，宽度各 26px）。**2026-10-18 按用户要求收窄为纯剪贴板语义**：点「复制」记下整行（名称、单位、数量、单价）→ 点「粘贴」写回**当前这一行**；不跳下一行、不自动建行、不抢焦点全选。
- **粘贴后换型号的价格处理（本版最关键的一条）**：粘贴来的单价属于上一行那个型号，而现有规则是「手输单价 > 0 会被学成该客户对该商品的专属价」。所以名称一旦不同于复制来源，代码会**先清空单价**再走正常取价流程 —— 有专属价自动带出，没有就留空手输，绝不让旧型号的价串到新型号上。拿货/退拿货填的是入库成本，同样按这条规则清价。
- **设置页「打印版面预览」只保留「生成预览 PDF」**：删除三个「预览打印效果」按钮及其 HTML 样张路由（`/settings/print-preview`、`/settings/print-preview/summary`、`/settings/print-preview/reconciliation`）。**PDF 那三个路由与共用 context 全部保留**（`test_phase4_print_isolation` 仍直接使用 `settings_print_preview_context`）。
- **设置页分区标题醒目化（2026-10-18 按用户三轮反馈定稿）**：分区标题条改为**圆角矩形卡片**（四角 12px 圆角 + 1px 描边 + 内阴影画左侧蓝条），标题 24px 主色，蓝色渐变按「明显看得见」的浓度（主色 22% → 9%(42%) → 透明(72%)），`position:sticky` 吸在 `.page-body` 顶部；**序号徽标已按用户要求删除**。连带修掉一个我自造的回归：标题条吸顶后会盖住下一个分区顶部 83px，导致左栏目录高亮**永远慢一格**（实测滚到「打印」仍亮「基本」）—— 用 `scroll-margin-top:92px` + 高亮阈值改为「标题条高 + 20」修正。
- **验证**：全量 `pytest` 与前端 `node --test tests/frontend/*.test.mjs`（含新增 `order_row_copy_paste.test.mjs` 8 项）见下；真实 Chrome + CDP 在隔离实例（独立数据根）实测四类开单：复制→粘贴→名称全选聚焦、改 DN65（该客户专属价 68.5）自动带出、改 DN80（无专属价）留空、按钮在单元格内且不溢出；设置页深浅两套主题截图核对，五个分区锚点跳转后左栏高亮逐项正确。
- **打包（2026-10-18，`打包Windows安装包.bat` 一次出齐）**：`dist/simple-erp-setup-v4.6.0.exe` 41,392,269 B（SHA256 `64b1b2f7ac9c2afe…`）、`dist/simple-erp-windows-v4.6.0.zip` 55,256,686 B（`4a1521b7cac9e7f7…`）、`dist/local-v4.6.0/简单ERP/`。**产物门**：ZIP 623 条目、顶层唯一 `简单ERP/`、无 `.db` / `demo_data` / 日志、包内 `VERSION` = `v4.6.0`；两个 EXE 的 PE 版本均为 `4.6.0.0` / `v4.6.0`；绿色版演示数据 **200 条**（销售 125 + 拿货 45 + 退拿货 30，`integrity_check`=ok）。**隔离冒烟（分发产物，非源码）**：`ERP_PORT=5097` + 独立数据根，`/health` = `v4.6.0`、4 条路由 200、开单页含复制/粘贴按钮、设置页序号徽标 0 / 「预览打印效果」0 / 「生成预览 PDF」3、旧 HTML 样张路由 404、**样张 PDF 200（25,848 B）** —— GTK 自动寻找在打包产物上同样生效。正式数据根 `erp.db` SHA256 全程保持 `d9108367…` 未变。旧 v4.5.0 三产物已清理。
- **发版证据（2026-10-18）**：5 个提交推 `main`（`1c540f1..3f5307d`，`git ls-remote origin refs/heads/main` = `3f5307dfb9ad…` 与本地 HEAD 一致）；annotated tag `v4.6.0`（tag 对象 `7582048a` 解引用回 `3f5307d`）；[GitHub Release v4.6.0](https://github.com/Astyyym/simple-erp/releases/tag/v4.6.0)（draft=false、prerelease=false、target=`main`）挂三个资产：`simple-erp-setup-v4.6.0.exe`（41,392,269 B，SHA256 `64b1b2f7…`）、`simple-erp-windows-v4.6.0.zip`（55,256,686 B，`4a1521b7…`）、`simple-erp-mobile-v1.1.0.apk`（43,140 B，`35789ed1…`）——Release API 的 `digest` 与本地 SHA256 一致，`gh release download` 取回后与本地产物 `cmp` **三个全部字节一致**。**push 前重跑全量**：`pytest tests/ -q` = 1018 passed / 2 skipped / 0 failed（310.60s）。
- **仍未做**：目标打印机实打**本版不需要**（不动打印版式）；桌面壳 WebView2 内的点击链路未在本次改动范围内。

## v4.5.0 - 手机端查得更透（商品/客户价全景、精确金额）+ 壳手感（2026-10-17，已发版）

- **商品价页改成「商品优先」**：去掉「先选客户」下拉，直接搜商品名/拼音；卡片显示「N 个客户 + 价格区间（如 `¥52 – ¥58.06`）」，整块可点。原先是按客户一张表、卡片纯展示，用户点不进去（就是他提的那个洞：「商品价格这里我点不进去，我不能看到每个用户的专属价格」）。
- **新增商品详情页 `/m/products/<id>`**：这个商品在**每个客户**处的专属价，价格降序，带更新时间与「差 ¥0.27」的差额提示；没有该商品专属价的客户单独给数目（如「另有 13 个客户没有这个商品的专属价」）。与桌面端商品编辑页的「客户专属价」卡片同口径。
- **客户详情新增「专属价」段 + 新增 `/m/customers/<id>/prices`**：客户 → 有哪些商品的专属价（前 3 条 + 看全部），与商品详情互为反向。已删客户仍列出但标「已删除」且不可点（桌面端也不过滤）。
- **列表金额不再分档，直接给具体数字**（`¥6,229.80`、`-¥503.13`）：v4.3.0 定的「精确金额只在详情页」被用户 2026-10-17 当场推翻——柜台前要看的是自己的数，不是别人的档位。分档标签（`1千–1万` / `无欠款` / `无金额`）从客户列表、单据列表、客户详情头部全部删除，页面里一个不剩。
- **右下角齿轮删除**，改地址收进「设置 → 电脑地址」一行（只在 APK 原生桥存在时可点，浏览器里保持静态，不留假入口）；配对页补「电脑地址不对？点这里修改」——那页没有底部 tab，不给出口就真没法改地址。
- **下拉刷新**：只在页面顶部接管手势（阈值 70px、300ms 防连刷、复用导航骨架、松手后吞掉补发的 click），桌面浏览器（无 `touchstart`）完全不注册。判断「在顶部」必须看 `.body.scrollTop`，只看 `window.scrollY` 永远为 0、列表滚到中间也会误触发。
- **APK 1.1.0（versionCode 2，签名不变可覆盖安装）**：窗口/WebView/状态栏/导航栏统一成页面底色，消掉冷启动白闪；**修掉一个真机才暴露的返回键 bug**——targetSdk 36 走 Android 预测返回，`onBackPressed()` 根本不被调用、详情页按返回直接回桌面，已注册 `OnBackInvokedCallback`（详情页→商品列表，首页连按两次退出）。
- **验证**：全量 `pytest tests/ -q` = **1020 passed / 0 failed**（基线 1003，新增 17 条：商品详情查询对拍 / 路由 200 / 只读保证）。模拟器（Android 16 / targetSdk 36）实测：装 APK → 配对 → 今日概览 → 商品价 → 点卡片进详情、15 个客户价降序 → 客户详情「专属价 3 个商品」；返回键两段行为；深色冷启首帧无白闪。下拉刷新用 headless Chrome + CDP 真 touch 事件验：顶部下拉 80px →「松手刷新」→ 骨架层 → `navigation type=reload`，滚到中间下拉不触发，300ms 内二次下拉被忽略，桌面无 `ontouchstart` 不注册。
- **产物与安装验收（2026-10-17）**：`dist/simple-erp-setup-v4.5.0.exe` **41,402,406 B**（SHA256 `610e4fa0…`）、`dist/simple-erp-windows-v4.5.0.zip` **55,290,247 B**（`767eeb50…`）、`dist/simple-erp-mobile-v1.1.0.apk` **43,140 B**（`35789ed1…`，versionCode 2 / versionName 1.1.0，DEX 内含 `OnBackInvokedCallback`、`SimpleERPNative`）。ZIP **680 条目**、顶层唯一 `简单ERP/`、无 `.db` / `demo_data` / 日志，包内 `VERSION` = `v4.5.0`，**18 个**手机端模板 / 静态文件与源码**逐字节一致**（含新增 `products/detail.html`、`customers/prices.html`）；安装包与绿色版 EXE 的 PE 版本均为 `4.5.0.0` / `v4.5.0`。**真实覆盖安装（正式安装包，v4.3.0 → v4.5.0，显式 `/DIR=D:\app\简单ERP`）**：679 个文件安装成功、PE 变 `4.5.0.0`、`config.json` **逐字节不变**（`bee3fcbb…`）、业务库 `data/erp.db`（`e8412c66…`）与 `mobile_devices.json` 一字节未动；装完启动 `/health` = `v4.5.0`、`auth: disabled`、数据根仍是「文档\简单ERP数据」、`/`、`/accounts/`、`/orders/new`、`/products/` 均 **200**，手机监听 `0.0.0.0:5001` 自动起来、`/m/pair` **200** 且含新文案「电脑地址不对？点这里修改」，蓝图外与未配对路径仍是 302。**人工复验（用户 2026-10-17 完成）**：目标打印机实打通过（本版不动打印版式）；手机真机装 `1.1.0` 覆盖安装、新页面逐页点过、千元机手感与横竖屏均通过。
- **发版证据（2026-10-17）**：提交 `77d7b4f` 推送 `main`（`git ls-remote origin refs/heads/main` = `77d7b4fd3714…`，`gh api .../commits/main` 同值）；annotated tag `v4.5.0`（tag 对象 `c53b9341` 解引用回该提交）；[GitHub Release v4.5.0](https://github.com/Astyyym/simple-erp/releases/tag/v4.5.0)（**Latest**）挂三个资产：`simple-erp-setup-v4.5.0.exe`（41,402,406 B，SHA256 `610e4fa0…`）、`simple-erp-windows-v4.5.0.zip`（55,290,247 B，SHA256 `767eeb50…`）、`simple-erp-mobile-v1.1.0.apk`（43,140 B，SHA256 `35789ed1…`）——Release API 的 `digest` 与本地 SHA256 一致，`gh release download` 取回后与本地产物 `cmp` **三个全部字节一致**。**远端树**：610 条目 / **544 blob** / 7.29 MB，`config.json` / `data/` / `demo_data` / `*.db` / `*.exe` / `*.apk` / `dist/` / `build/` 命中 **0**（仅 `packaging/seed_demo_data.py` 按设计在库）。**安装器生命周期复验（发布后补做，2026-10-17，正式安装包）**：装到 `D:\app\简单ERP-probe450`（PE `4.5.0.0`、`LastAppDir` 指向它）→ 写 `config.json` 标记 → 卸载（卸载键删除、`LastAppDir` 保留、配置留存）→ **不带 `/DIR` 重装落回该目录**、`%LOCALAPPDATA%\Programs\简单ERP` 未被创建、`config.json` 哈希等于标记 → 清理探针 → 正式安装包重装回 `D:\app\简单ERP`：`config.json` 仍 `bee3fcbb…`、EXE 仍 `81e73359…`、卸载键与 `LastAppDir` 均指回真实目录。**注意**：安装器检测到同名 `简单ERP.exe` 在运行会弹「关闭并继续」框，静默安装也不跳过，因此复验是等实例关闭后继续的——全程未 taskkill 任何进程。

## v4.4.0 - 安装目录记忆（卸载后重装不再落回默认目录）（2026-10-17，已发版）

- **修掉一个会让人以为「配置丢了」的安装器缺陷**：装到非默认目录（例如 `D:\app\简单ERP`）→ 卸载 → 重装，安装器原本会静默落回 `%LOCALAPPDATA%\Programs\简单ERP`，并在那里生成一份**全默认 `config.json`**。用户看到的是店铺名、打印偏移、主题、手机访问开关全部变回默认——原件其实还在旧目录，业务数据一个字没动。根因：Inno 把「上次安装目录」存在自己的卸载注册表键 `HKCU\...\Uninstall\{AppId}_is1` 里，卸载把这个键整体删掉，重装时没有任何记录可用。
- **改法**：安装完成（`ssPostInstall`）时把本次 `{app}` 写进 `HKCU\Software\简单ERP\LastAppDir`——这个键不归卸载器管，卸载不会删；`DefaultDirName={code:GetLastAppDir}` 优先读它，读不到才回退 `{localappdata}\Programs\简单ERP`（回退值必须非空，否则向导会拿到空目录名）。
- **实测（真实安装器，不是单测）**：装到 `D:\app\简单ERP-test` → 卸载（卸载键删除、`LastAppDir` 保留）→ **不带 `/DIR` 重装仍落回 `D:\app\简单ERP-test`**，`config.json` 逐字节不变（`065e90c9…`），`%LOCALAPPDATA%\Programs\简单ERP` 未被创建；已安装状态下覆盖安装（升级路径）落点与配置不变；删掉注册表键后全新安装仍正确落到默认目录；用户真实安装 `D:\app\简单ERP` 全程未被测试触碰（`config.json` 仍 `bee3fcbb…`）。
- **守护测试**：`tests/test_installer_packaging.py::test_installer_remembers_previous_install_dir_across_uninstall` 钉住四点——读自建键、非空 per-user 回退、`ssPostInstall` 写 `{app}`、该键不得进 `[UninstallDelete]`。
- **验证**：全量 `pytest tests/ -q` = **1003 passed / 0 failed / 0 skipped**（基线 1002，新增 1 条）。
- **不变**：per-user 安装（`PrivilegesRequired=lowest`，不允许切换提权）、升级不覆盖 `config.json`、卸载不删业务数据、`[UninstallDelete]` 只碰 `{app}` 下安装残留——这几条契约原样保留，守护测试同步照旧。
- **发版证据（2026-10-17）**：提交 `b5a0bc8` 推送 `main`（`git ls-remote origin refs/heads/main` = `b5a0bc8ce95f…`），annotated tag `v4.4.0`（tag 对象 `25752ece` 解引用回该提交）；[GitHub Release v4.4.0](https://github.com/Astyyym/simple-erp/releases/tag/v4.4.0)（**Latest**）挂两个资产：`simple-erp-setup-v4.4.0.exe`（41,393,977 B，SHA256 `438c9542…`）、`simple-erp-windows-v4.4.0.zip`（55,281,765 B，SHA256 `71959fa5…`）——`gh release download` 取回后与本地产物 `cmp` **字节一致**、SHA256 相同。**产物门**：ZIP 678 条目、顶层唯一 `简单ERP/`、包内 `VERSION` = `v4.4.0`、无 `.db` / `demo_data` / 日志；安装包与绿色版 EXE 的 PE 版本均为 `4.4.0.0` / `v4.4.0`。**隔离 EXE 冒烟**（ZIP 解压副本 + 临时 `ERP_DATA_ROOT` + `ERP_PORT=5099`）：`/health` = `v4.4.0`、`auth: disabled`、`data_root` 指向临时目录、`database: ok`，`/` 与 `/accounts/` 均 200。**跨版本升级实测（用正式安装包）**：v4.3.0 安装包铺底（PE `4.3.0.0`）→ 改 `config.json` 造差异 → 用 v4.4.0 安装包覆盖安装（不带 `/DIR`）→ **同目录**、PE 变 `4.4.0.0`、`config.json` 逐字节保留、默认目录未创建；卸载后 `LastAppDir` 与 `config.json` 均保留；不带 `/DIR` 重装仍落回原目录且配置不变。用户真实安装 `D:\app\简单ERP` 全程未被触碰（`config.json` 仍 `bee3fcbb…`）。**仍未验**：目标打印机实打（本版不动打印）、微信内置浏览器与 APK 壳（属 v4.3.0 手机端遗留项）。
## v4.3.0 - 手机只读端（浏览器直连，与电脑同一 WiFi）（2026-10-17，已发版）

- **新增手机只读端**：手机浏览器连同一 WiFi 打开 `http://<电脑局域网IP>:5001/m/`，只用**只读**方式查账——今日概览、客户（欠款与最近流水）、单据（含年/月/日筛选）、商品价（该客户专属价 + 最近成交）、设置。**不能开单、不能编辑、不能作废**，所有改动仍只能在电脑上做。本版**不做 APK**（用户 2026-10-17 定案：先用浏览器直连跑 2–3 周，确认真的会用再包壳）。
- **为什么是第二个监听，而不是把主应用开放到局域网**：主应用仍**硬绑 `127.0.0.1`**、物理上不能被手机访问；手机走同进程里的**独立 Flask app**，只挂 `mobile_bp`，桌面端路由在它上面**天然 404**（不是靠 `if` 拦）。端口固定 **5001**，被占就开关回弹并明确报错，**不自动换端口**（端口是配对地址的一部分，漂移会静默失联）。
- **只读有三层保证**：蓝图级只放 GET（唯一例外是配对提交 `POST /m/pair`，它只写 `data/mobile_devices.json`，绝不写 `erp.db`）；所有查询走 SQLite `mode=ro` + `busy_timeout`（主应用 8 线程写的时候手机读不会 `database is locked`，写语句在数据库层就抛错）；6 条 pytest 钉住「蓝图外 404 / 非 GET 405 / 未配对 302 / 已配对页面无写入口 / 批量余额与权威实现对拍 / 物理只读」。
- **配对**：桌面端「设置 → 手机访问」打开开关 →「生成配对码」（码**就地显示在按钮下方**），手机输入 6 位数字配对。码只有 **120 秒、只能用一次**，每分钟最多生成 3 次，连错 5 次锁 5 分钟；配对后手机靠 cookie 免输，**手机丢失可在这里单独撤销**（撤销即彻底失联——阶段一手机不落任何本地数据）。
- **今日概览口径**：销售 / 退货 / 拿货 / 退拿货**四类全算**，按四类分组（某类为 0 仍占一行）+ **卖货净额 / 进货净额两段小计** + 当日时间轴；**不做四类相加的总计**（那等于把收入与支出混加，既不等于收到的钱、也不等于利润）。
- **金额显示**：列表只给分档——客户列表按欠款（无欠款 / <1千 / 1千–1万 / >1万），单据列表按金额绝对值给中性档；**精确金额只在详情页出现**（柜台前手机被隔壁客户看到，精确欠款会得罪人）。全程**不显示任何成本或利润**。
- **设置页新增「手机访问」**：开关、访问地址、二维码（本地生成、不联网）、生成配对码、诊断信息（监听地址 / 端口自检 / 最后一次手机成功请求 / 上次错误）与已配对设备列表 + 撤销。**手机端设置页没有任何写端**：主题只是本机 localStorage，「重新配对」只是跳转。
- **顺带改的三处接口接缝**：`get_customer_account_ledger()` 增加只读连接参数（余额口径仍只有这一份实现）；`_purchase_today_history()` 增加可注入的详情链接构造（独立 app 里没有 `purchases` 蓝图，`url_for` 会抛 `BuildError`）；`qrcode` 进 `requirements.txt` 与两个 spec 的 `hiddenimports`（模板与静态资源本来就整目录打包，新增测试守住这条）。
- **验证**：全量 `pytest tests/ -q` = **1002 passed / 0 failed**（基线 957，新增 45 条）。**真机实测（安卓 + Chrome，同一 WiFi）**：配对通过、各页预览正常、「添加到主屏幕」后从桌面图标点开**不需要重新配对**、微信内置浏览器能打开同一地址、手机断开 WiFi 看到浏览器原生错误页（预期）、**手机上今日单据与金额与电脑一致**。真实 socket 自测 14/14（用局域网 IP 而非回环）：未配对 302、配对页 200、配对提交 302 + cookie、各页 200、静态资源 200、蓝图外 404、非 GET 405、停止后端口释放。
- **本版踩到并写进项目记忆的坑**：① 取局域网地址不能用「连公网看默认路由」——本机装了 TUN 代理，那样拿到的是代理网段假地址，二维码会显示手机永远连不上的地址；改为合并主机名解析、滤掉代理与链路本地网段后再按私网优先级取。② Windows 上 `SO_REUSEADDR` 允许重复绑定已在监听的端口，`bind` 不报错，**端口占用必须用 connect 探测**。③ 真实手机连上后才发现的两个交互缺陷（配对码在页顶看不见、生成后页面滚回顶部）当轮修掉并补了回归测试。
- **发版证据（2026-10-17）**：提交 `05be88b` 推送 `main`（`git ls-remote origin refs/heads/main` = `05be88b2ff83…`），annotated tag `v4.3.0`（tag 对象 `30d155f6` 解引用回该提交）；[GitHub Release v4.3.0](https://github.com/Astyyym/simple-erp/releases/tag/v4.3.0)（**Latest**）挂两个资产：`simple-erp-setup-v4.3.0.exe`（41,386,554 B，SHA256 `dfc6f92cc9089f543122d7974a6b9cbd4431ba988476cd58436c797cc2d9f815`）、`simple-erp-windows-v4.3.0.zip`（55,281,958 B，SHA256 `b45cc1debc622becb6a50229552b1ded33ec12464c298cb5f8af819d1bf5c2bc`）——`gh release download` 取回后与本地产物 `cmp` **字节一致**，Release API 的 `digest` 与本地 `sha256sum` 相同；远端树 **521 blob / 6.83 MB**，`config.json` / `data/` / `demo_data` / `*.db` / `*.exe` 命中 **0**。**真实 EXE 冒烟**（用 ZIP 同款产物复制到隔离目录 + 临时 `ERP_DATA_ROOT`）：`/health` = `v4.3.0` 且数据根指向临时目录、第二监听 5001 随开关自动起来、`/m/pair` 200、蓝图外 **404**、非 GET **405**、退出后端口释放——**8/8 通过**；包内 **16 个**手机端模板 / 静态文件与源码**逐字节一致**；PE 版本资源含 `4.3.0.0`。ZIP 与 v4.2.0 相比 **661 → 678 条、消失 0 条**（新增 16 个手机端资源 + 1 个 Pillow imagingft），根目录仍是 `简单ERP/`，不含演示数据。**未做**：安装包真实安装 / 升级 / 卸载链路、目标打印机实打。
- **未验**：**升级 / 卸载**链路 → 2026-10-17 已在 v4.4.0 发版时用正式安装包验掉（v4.3.0 → v4.4.0 覆盖升级同目录、配置保留；卸载后重装落回原目录，见 v4.4.0 条目）；目标打印机实打（本版不动打印版式）；**微信内置浏览器已验「能打开同一地址」**（2026-10-17 真机，见上方真机验收）；微信内「添加到主屏幕」与微信 cookie 是否与系统浏览器隔离**按已知限制关闭**（用户 2026-10-17 判断：微信没有该入口、cookie 容器隔离不由本项目控制，交付说明固定为「要像 App 一样用请用系统相机或 Chrome 扫码」）；千元机手感。
- **发布后装真机复验（2026-10-17）**：用户用发布页的安装包装到自选目录（Inno 卸载器 `unins000.exe` 存在，确认走的是安装向导），**安装与启动均无弹窗**；在设置页点「打开手机访问」让程序首次监听 `0.0.0.0:5001` 时弹出一次 Windows「网络」提示，点「允许」后系统写入两条入站 Allow 规则，**手机扫二维码直接连上**。手机不需要重新输配对码——设备记录在数据根的 `mobile_devices.json`（卸载不删数据）、cookie 在手机浏览器里，属于预期行为；因此设置页同时显示了两条同一台手机的记录（重复配对会新增记录），撤销多余一条即可。**源码模式验不到这个弹窗**（socket 实际由 venv 背后的基础解释器持有，那条路径早就有允许规则）。
## v4.2.0 - 客户/商品筛选改模糊联想输入 + 分析页筛选区重排 + UI 批次 2 收口（2026-10-17，已发版）

- **账款管理 / 往来对账的客户字段：原生下拉 → 模糊联想输入。** 输入即匹配（名称子串 / 拼音首字母 / 全拼），候选浮层点选或回车选中；提交值仍是 hidden `customer_id`（收款、调整、作废、对账入口与对账快照全按 id 工作，链路未动）。只输入名称、没点候选时明确提示「请从下拉候选里选择…」，**不猜客户**；选中后再改字会清空已选 id，避免「改了名字却按上一个客户查」。复用共享组件 `ErpOrderEntry.bindAutocomplete`（原生 `<datalist>` 在 WebView2 不渲染弹层，不得回退）。
- **数据分析页的客户与商品名称同样改联想输入。** 商品候选模糊、**选中项仍是精确单品**（`summarize_analytics` 继续用 `p.name = ?`）；半截名未点候选时给明确提示，不再静默返回空结果（避免「明明有销售却查不出」的假象）。顺带删除无人消费的全量商品 JSON（`#analysisProductOptions`）与 customers/products 全量下发。
- **分析页筛选区重排：客户 / 商品名称移到日期框右侧同一行。** 时间块改用 grid 两列排成「模式按钮一行、起止日期一行」（复刻单据管理页形态），宽度 **596 → 296px**，腾出的宽度正好让客户 + 商品并排在日期框右侧；1258px 窗口实测即可一行（时间块 296 / 客户 179 / 商品 179，横向溢出 0），无需压窄输入框、也未改时间模式按钮的交互形态。窄档（≤1180px）仍自动折行。CSS 版本串 `?v=4 → ?v=5`。
- **顺带修掉两处既存缺陷（同一根因）**：`page-refresh.mjs` 的 `refreshInPlace` 只替换「当前文档里已存在」的同 id 区块——① 账款管理「选完客户点『查询流水』没反应」（用**未改动的旧实例**做过对照实验确诊，非本轮引入）；② 分析页「首次筛选看不到已选条件 chip」。修法分别是「空态与结果区共用同一 fragment id」与「chip 容器常驻 + `.is-empty` 隐藏」。**未修（范围外）**：单据管理页 `#ordersChips` 同结构同问题。
- **含前序未提交的「UI 批次 2」整批**：单据管理 / 数据分析 / 设置三页功能分类分区（同层级元素分组常显，不做折叠）、回收站两区动作归位、分析页金额汇总溢出修复（`.analytics-summary` 残留 3 列网格）与筛选区重排、设置页「保存设置」卡片与分区卡片分离。
- **验证**：全量 `pytest tests/ -q` = **956 passed / 1 skipped / 0 failed**；新增 `tests/test_accounts_customer_autocomplete.py`（8 项）与 `tests/test_analytics_filter_autocomplete.py`（6 项）；4 个既有测试文件随契约演进改断言（**改断言不删用例**）。隔离实例 + headless Chrome CDP 真实交互实测：真实键盘输入 → 真实鼠标点候选 → 查询，覆盖客户/商品联想、拼音首字母候选（13 条）、半截名提示、换客户 chip 跟随、对账页首次查询、1258/1578 两档筛选区几何。
- **本地私有**：短计划见 `开发短计划/2026-10-17_客户与商品筛选改模糊联想_执行计划.md`（不随公开仓库发布）。

- **发版证据（2026-10-17）**：提交 `6c41b99` 推送 `main`（`git ls-remote origin refs/heads/main` = `6c41b9950e56…`），annotated tag `v4.2.0`（tag 对象 `dd2c1f23` 解引用回该提交）；[GitHub Release v4.2.0](https://github.com/Astyyym/simple-erp/releases/tag/v4.2.0)（Latest）挂两个资产：`simple-erp-setup-v4.2.0.exe`（40,491,385 B，SHA256 `6d98e9e321dc65ed258c6230634845ad5730ebca57e86808ea19f915a5f3da0f`）、`simple-erp-windows-v4.2.0.zip`（54,087,630 B，SHA256 `60879660f4f709b6d82a08c3847d86a047dc98ade516a778db9b4558957b72cf`）——`gh release download` 取回后与构建产物 `sha256sum` 相同且 `cmp` 字节一致；远端树 531 条目 / 489 blob，`demo_data` / `*.db` / `*.exe` / 根 `config.json` / `开发短计划/` 均 0 命中（仅出厂模板 `packaging/default_config.json` 与 `packaging/seed_demo_data.py` 按设计在库）。**发版前重跑**全量 `pytest tests/ -q` = **956 passed / 1 skipped / 0 failed**（`ERP_PORT=5099`，306.76s）；**真实 EXE 冒烟**（ZIP 解压到隔离副本 + 临时 `ERP_DATA_ROOT`/`ERP_CONFIG_PATH`）：`/health` = `v4.2.0` 且 `auth: disabled`，`/`、`/accounts/`、`/accounts/reconciliation`、`/analytics/`、`/orders/new` 全 200 且包内页含 `bindAutocomplete`，包内「本轮改动过的模板 / 静态文件」与源码**逐字节一致**（27 个改动文件，PY 模块随 EXE 内嵌）。**本次推送同时带上未单独发版的 v4.1.0 源码**（v4.1.0 无独立安装包，其改动全部包含在 v4.2.0 产物内，对用户以 v4.2.0 一次性交付）。安装包真实安装 / 升级 / 卸载链路与目标打印机实打仍未做。

## v4.1.0 - 恢复旧版备份的代价提示与界面说明文字开关（2026-10-16，本地打包；未单独发版，源码随 v4.2.0 推送至 main）

- **备份与恢复：恢复旧版备份前先看见代价。**备份列表新增「数据库版本」列——读备份包内 `erp.db` 的 `schema_version`（`utils/backup.py` 新增 `read_backup_schema_version()`，读不出按旧版处理）；版本低于当前的标「旧版结构」，并在**行内红字 + 恢复确认框 + 恢复成功提示**三处说明：恢复并重开程序会自动升级到当前版本，**库存与成本数据将被永久删除、无法回退**（v15 升级不可逆，回退只能靠迁移前自动生成的整库备份）。
- **设置页「显示」新增「界面说明文字」开关**（配置项 `ui_show_hints`，默认开启）：关掉后隐藏各页面的提示 / 说明 / 注意类小字。实现为 `base.html` 的 `<html data-ui-hints="on|off">` + 一条 `html[data-ui-hints="off"] [data-ui-hint]{display:none!important}`，模板里 76 处说明段**逐处显式**标记 `data-ui-hint`（不按 class 一刀切：`text-muted small` 这批 class 里混着导入原因、更新时间戳等数据单元格）。**不纳入**：危险操作警告（「此操作不可撤销」、旧版备份警告）、状态与数据文字（计数 / 金额 / 日期 / 当前状态）、空态提示、`placeholder`、按钮与弹窗正文。设置页复选框未勾选时浏览器不提交该字段，服务端按「在场即开、缺席即关」解析。
- **v15 删除库存链后的清空 / 备份 / 导入同步核对**：实测确认清空清单已不含库存三表（表已物理删除）、备份是整库文件级快照（与表结构无关）、商品导入已不接受「期初数量 / 期初成本」两列；仅修掉一处过时 docstring（`routes/products.py` 导入确认函数不再写「带期初的行独立事务初始化」）。
- **验证**：全量 `pytest tests/ -q` = **942 passed / 1 skipped / 0 failed**（`ERP_PORT=5099`，412s）；前端 `node --test tests/frontend/*.test.mjs` = **fail 0**；新增 `tests/test_ui_hints_toggle.py`（4 项）与 `tests/test_backup_restore.py` 的 4 项备份库版本用例。真实浏览器验收（headless Chrome 同源 iframe 读 `getComputedStyle`，隔离实例）：关掉开关后开单页 **5/5**、设置页 **14/14**、回收站 **5/5** 标记元素 `display:none`，打开开关时 0 隐藏；同一页危险警告 `danger_hidden=0`（关掉开关也始终可见）。
- 顺带：出厂模板 `packaging/default_config.json` 补 `ui_show_hints` 键（键值必须与 `CONFIG_DEFAULTS` 一致）；`tests/test_master_data_list_layout.py` 的图例断言改为容忍属性插在 `class` 之前（**改断言不删测试**）。文档同步：需求文档「系统设置」行补开关口径，新增项目 reference `skills/simple-business-erp-development/references/ui-hint-visibility-toggle.md` 并挂进 references 索引。
## v4.0.0 - 库存与成本链整体移除（schema v15 物理删表删列）（2026-10-16，已发版）

- **用户决策（2026-10-16 逐项拍板 8 条）**：① 拿货 / 退拿货**保留为纯流水单据**（单据、打印、`NH` 单号、对账并入、账款抵扣、回收站、当天历史全部照旧；不再动库存、不再产生成本）；② **成本 / 利润链全删**（计价引擎、成本快照写入、毛利润/毛利率、成本诊断、历史成本补录、毛利润排行、开单页成本列与毛利润预览全部下线）；③ **物理删表删列 + 写迁移（schema 14 → 15）**，老库不可逆、不能回退到 v3.15.0 程序（用户已知并接受，回退只能靠迁移前自动生成的整库备份）；④ **单据编辑锁全部放开**——已过账锁、补录锁、正式拿货/退拿货锁全撤，四类单据除「已作废」外都可编辑；⑤ 绿色版演示数据**维持 200 条、重做组成**，去库存/成本内容、保持「打开即用」；⑥ 导入**硬删**「期初数量 / 期初成本」列（含这两列的旧文件整表报「表头错误」）；⑦ 商品**安全库存** UI 与代码一并删除、列随 v15 迁移删除；⑧ `designs/simple-erp-inventory-analytics/` 整目录及引用它的 2 个前端测试一并退休。同日第二轮补充定案：`products.default_price_cents` 与 `purchase_return_orders.posted_once` **均删**。

- **数据层（schema v15）**：物理删除 3 张表 `inventory_postings` / `inventory_initializations` / `product_inventory_state`，9 个列 `order_items.unit_cost_micro|cost_total_micro|cost_source`、`products.safety_stock_3dp|default_price_cents`、`purchase_return_items.unit_cost_micro|cost_total_micro|posting_seq`、`purchase_return_orders.posted_once`。迁移顺序为「先自动整库备份 → 重建表 → `foreign_key_check`」；**老库升级不可逆**，程序不回退到 v3.15.0 读写同一库。实测：迁移后库内 18 张表**不含任何库存表**，`integrity_check` = ok、`foreign_key_check` 为空，四个关键表的列白名单**零已删列**。

- **服务 / 路由 / 前端层**：删除模块 `erp.services.inventory` / `inventory_status` / `profit` / `cost_backfill`（含其模板 `orders/cost_backfill.html`），删除函数 `initialize_product` / `summarize_profit` / `backfill_order_costs` / `project_inventory_status`（新模块 `erp.services.purchases` 承接 `create_purchase_order` 等）。`create_product(name, unit, pinyin_initials="", *, pinyin_full="", brand=None)` **无 `safety_stock` 参数**。顺带修掉三处真缺陷：`routes/orders.py` 列表页残留 `cost_missing` 引用（会 `NameError` 500）、`utils/filter_chips.py` 的「成本完整性」chip、`routes/purchases.py:edit_return` 仍以 `status != 'draft'` 拒绝正式退拿货单编辑（违背决策 ④）。**代码级残留扫描（`tokenize` 剔除注释与字符串）在 `app/erp` 内 0 命中**；剩余的明文命中全部是「必须提到被删概念」的三类：迁移模块、模板里的 Jinja 注释、以及**要明确告诉用户「已不再接受期初两列」的导入提示文案**。

- **UI 层**：开单表头由 8 列收为 7 列（去「成本(元)」），成本列 / 毛利润预览 / 库存状况 / 期初 / 安全库存 / 补录入口 / 「成本诊断」页签全部移除；`analytics/index.html` 重写为热力图 + 产品排行两页签，`analytics-v4.css` 按 selector 删掉 48 条 `cost-*` 规则与 `costsweep`/`stackgrow` 关键帧、汇总卡 5→3 列、商品表 `min-width` 1400→520px（花括号 98/98 配平）。**模板内联 JS 检查 15 块 / 0 语法失败**（新增脚本 `check_template_inline_js.py`：模板里被删列/删控件破坏的自身 JS **仍会返回 HTTP 200**，只有语法检查能抓住）。

- **演示数据重写（`packaging/seed_demo_data.py` 1448 → 1326 行）**：删 `ProductSpec` 的 `init_qty/init_cost/safety/kind`、`build_inventory()` 期初建档整块、`self.stock` 库存跟踪与三个 `_apply_*_stock` 方法（14 处调用）、人工补录段。**四类比例与总数守恒**：实测 `sale 90 + return 35 + purchase 45 + purchase_return 30 = 200`，28 个往来对象、43 个商品。`verify()` 的「库存守恒」两段换成**金额守恒**（单头金额 == 明细小计之和、退货为负号口径）、**金额方向**与 `foreign_key_check`。顺带修掉一个原脚本就存在的潜在崩溃：部分退在来源行剩余不足 1 个计量单位时 `randint(1, remaining // 1000)` 会抛 `ValueError`（删掉库存守卫后更易触发）。

- **测试层收口（65 个文件，+840 / −2837）**：先脚本化处理机械部分（37 个文件里指向已删 `services.inventory` 的 import 重指到 `services.purchases`、剥掉 60+ 处 `initialize_product` 调用），再把剩余失败切成 5 份并行修。删除 9 个纯库存/成本测试文件 + 3 个前端测试 + `designs/simple-erp-inventory-analytics/`；决策 ④ 的相关断言**反转而非删除**（「正式拿货单编辑页 400」→ 200；「当天历史不出现编辑入口」→ 出现）；断言列数改为**从被断言对象自身推导**（读模板 `<thead>` 的 `<th>` 个数），不再写常量。**全量 `pytest tests/` = 934 passed / 1 skipped / 0 failed**（`ERP_PORT=5099`，178.9s，收集数 935）；**前端 `node --test` = 94 passed / 0 failed**。反作弊核查：无 `assert True`、无新增 `skip`/`xfail`，`pytest.raises` 仍有 63 处。

- **真实浏览器关键路径（headless Chrome + CDP，隔离实例，非源码层）**：销售单新建手输单价 25.5×3 保存后 `MD202610160001` **落库并在列表页可见**；销售单详情有编辑入口，点「保存修改」改数量 3→5 后金额 **76.50 → 127.50**；拿货先建档商品再选候选对象/商品，保存后 `NH202610160001` 落库；当天历史显示 `NH202610160001 … 160.00 正式保存` 且**「查看」与「编辑」入口同时存在**，点进去改数量 2→3 后金额 **160.00 → 240.00**；全站 10 个页面 **0 个 JS 异常**、渲染正文对「毛利润 / 毛利率 / 库存 / 成本 / 告急 / 补录 / 移动平均 / 安全库存 / 默认价 / 启用状态 / 常规价」**全为 0 命中**。

- **产物与打包证据（`打包Windows安装包.bat` 一次跑完 5 步全通过，`VERSION` = `v4.0.0`）**：`dist\local-v4.0.0\简单ERP\`（**663 文件**，自带 200 条演示数据，打开即用）、`dist\simple-erp-setup-v4.0.0.exe`（40,477,639 B，SHA256 `aa830869797972994a2547071ec6b1f2183bd09366c02419a1130bd603e72ec4`）、`dist\simple-erp-windows-v4.0.0.zip`（54,070,153 B，**661 条目**，SHA256 `43e6056ba15853d0ad562e7ee040bfc5eb49fa2124096f78ae99b2d2d4bb21ee`）。**产物门（逐项实测）**：干净产物 661 文件与 ZIP 661 条目**双向一一对应、零差**；两者均**零** `demo_data` / `*.db` / `-wal` / `-shm` / `*.log`；ZIP 内 EXE 与干净产物 EXE **同哈希**（`c1616fc6…`）、绿色版 EXE 哈希不同（`fa826d4f…`）→ 两个入口确实分开；三份 EXE 的 PE 产品版本均为 `4.0.0.0`（UTF-16-LE 检索）。**绿色版 `demo_data`**：18 张表**不含库存三表**、`schema_version` = 15、`integrity_check` = ok、`foreign_key_check` 为空、四类合计 **200**、商品 43 / 客户 28 / 专属价 209 行；`演示数据说明.txt` 零禁词。**真实 EXE 冒烟（非源码）**：绿色版从**副本**启动（交付产物不被写脏），`/health` = `v4.0.0` 且 `data_root` 指向副本内 `demo_data`，首页 + 四类开单 + 客户 / 商品 / 账款 / 分析 / 回收站 / 设置**共 12 条路由全 200**；交付版用独立 `ERP_DATA_ROOT`/`ERP_CONFIG_PATH` 启动，`/health` = `v4.0.0`、`database` = ok、7 条路由全 200、隔离库正常建立。**包内取价口径实测**（真实 EXE 的 `/orders/api/products`）：有专属价 → `unit_price=52.00` / `is_customer_price=true`；无客户 → `unit_price=null`（**不再回落任何档案价**）。**正式数据根（`Documents\简单ERP数据`，9 文件）冒烟前后 size / mtime 逐项零变化。**

- **文档与记忆同步**：`AGENTS.md`（`default_price_cents` 旧规则改为 v15 已物理删除的 9 列 + 3 表完整清单与「不得写回代码 / 老库升级不可逆」口径）、`README.md`（「进销存」定位改为「轻量开单与账款系统」、`库存、双向往来与数据分析` 整节重写为 `拿货/退拿货、账款与分析` 并首条声明本次移除与不可逆、绿色包描述去「期初」、升级节改为不可逆警示）、`docs/需求/消防器材店ERP系统-需求文档.md`（§3.8 整段重写为「单据、账款与数据分析的当前基线」并新增「已撤销的旧基线（不得再实现）」块，§3.9 加历史横幅）、`docs/需求/库存成本与对账业务契约.md` → 改名 **`拿货与对账业务契约.md`** 并收缩正文、项目技能 `SKILL.md` 与 4 份 references（退休 `cost-backfill.md`、重写 `analytics-visualization.md`、订正 `purchase-return-decoupling.md` 5 处）、`memory/project-context.md` 与 `memory/pitfalls.md`。

- **本轮未做 / 未验**：`git push`、打 tag、建 GitHub Release 已在本版发版时补齐（提交 `c3a9db2` 推送 `main`、`tag v4.0.0` 解引用回该提交、Release 挂 `simple-erp-setup-v4.0.0.exe` + `simple-erp-windows-v4.0.0.zip` 两个资产）；**安装包真实安装 / 升级 / 卸载链路**未做；**目标打印机实打**未做（本版不改打印版式，四类开单 241mm 连续纸 / 对账单 A4 竖版规格保持原样）；**WebView2 桌面壳内点击链路**未在本版复验（本版在真实 Chrome 完成浏览器层验收，桌面壳接线由 `tests/test_desktop_*.py` 覆盖且全绿）。

- **必须记录的两件事**：① 一个子代理在跑权限探针时用 `open(path,"w")` 误把 `tests/test_purge_all_data.py` 截断为 0 字节，已按截断前读入**逐字节还原**（字节数一致、复跑得到与基线相同的失败集合作为证据），教训已写入全局技能 `tool-discipline`；② 本轮派出的子代理**越权**给 4 个**全局**技能（`tool-discipline`、`repository-engineering`、`phase-gated-erp-delivery` 及其新增脚本 `scripts/check_template_inline_js.py`、`project-development-delivery-discipline/references/implementation-and-parallel-execution.md`）追加了从本次交付提炼、已泛化去项目细节的教训——内容准确但与「全局技能除明确要求外不主动修改」的约定冲突，已如实上报，待用户裁决保留或移除。

## v3.15.0 - 商品售价来源收敛（默认价下线）与客户专属价查看（2026-10-16，打包发版）

- **默认价下线（用户决策）**：商品档案不再有「默认价」——建档、编辑、批量导入、导出、回收站、列表外显全部去掉（`create_product` / `update_product_record` 签名收窄去掉价格参数；导入模板列去掉「默认价」，带该列的旧文件仍可导入但该列被忽略；**新模板表头必须同步加进 `PRODUCT_HEADERS` 白名单**，否则下载的模板自己导不回来——实测踩过）。`products.default_price_cents` 列**保留、值恒 0、代码不再读**（不删列 = 不写迁移；该列 `NOT NULL` 且无默认值，故建档 INSERT 仍须显式写 0，`grep -rn "default_price" app/erp/` 不会、也不应字面归零）。
- **售价唯一来源 = 开单学习出的客户专属价**：`price_for_customer` 无专属价返回 `None`，`create_order`（Excel 导入开单）无价**明确报错**「该客户对此商品暂无专属价，请在导入文件中填写单价」而不静默落 0；`/orders/api/products` 只给客户专属价、无价给 `null`、不再下发「常规价」；`_learn_customer_price` 改「手输价 **> 0** 就写」（0 元不学）并被「选中已有商品」路径复用；**开单自动建档的商品也学习**（否则第一单的价丢了，下一单还是空的）。
- **换客户清空**：`refineProductRow` 无专属价时**清空**而不是保留旧值；点选商品不再写任何档案价。原有「往来对象变化就按新客户重算」的机制（`ErpOrderEntry.onCounterpartyChanged()`）不变，页面加载仍不触发——**编辑老单不清空已存行价**这条护栏守住。
- **删「常规价」UI**：销售 / 退货页复选框、拿货 / 退拿货页参考按钮与内嵌目录里的档案价一并删除（拿货单价是入库成本，本就不该有售价候选）。
- **删「启用状态」显示**：商品列表列与编辑页只读格删除（未启用仍由「库存状况」显示为「未启用」）；库存数量 / 成本 / 期初 / 告急 / 利润分析 / 分析中心全部保留、一字未动。
- **专属价查看**：商品列表新增「专属价」列（列内容固定为「查看专属价格」入口 → 编辑页锚点），「编辑」按钮改名「查看详情」；商品编辑页主栏新增「客户专属价」卡片（客户 / 专属价（元）/ 更新时间 + 「暂无客户专属价」空态）。删死模板 `templates/customers/prices.html`；`set_customer_price` 按用户要求**保留**（绿色版演示数据造数入口，无任何页面入口）。
- **本轮验收**：全量 pytest **1001 passed / 16 skipped / 0 failed**（`ERP_PORT=5099`，218s）、前端 Node **106 passed**、5 份模板内联 JS `node --check` 0 失败。隔离实例真实 Chrome 实测：档案侧「新增 → 列表 → 编辑 → 导入（预检 + 确认）→ 导出 → 模板 → 回收站」20/20；开单取价与学习十条全绿（新客户 + 新商品留空、手输 80 保存即学习、再开单自动带 80、换无价客户清空、换有价客户切 85、手输 0 不学、先商品后客户仍带价、联想下拉无客户价时不显示价格、退货原单带入行价不受影响而同页手选行走新口径、拿货 / 退拿货页不受牵连）；专属价卡片 3 行金额与 `customer_prices` 逐行一致 + 空态 + 列表入口落点正确。`packaging/seed_demo_data.py` 仍完整跑通，绿色版演示数据 **200 条**（销售 125 + 拿货 45 + 退拿货 30）。**未验**：WebView2 桌面壳内重跑（本轮在真实 Chrome 验）、目标打印机实打（本版无打印改动）。
- **产物与本地打包证据（2026-10-16，`打包Windows安装包.bat` 一次跑完 exit 0，214s）**：`dist\local-v3.15.0\简单ERP\`（**663 文件**，自带 200 条演示数据，打开即用）、`dist\simple-erp-setup-v3.15.0.exe`（40,153,916 B，SHA256 `a0e1df76eea6f5efdaab5a034c7d336a358e8ab973fae7ca0f2a095b62f28e7e`）、`dist\simple-erp-windows-v3.15.0.zip`（53,625,249 B，**661 条目**，SHA256 `5d2e2e97200d0ff3a3a0c6d3f2a3bdccc527cf89052fe58c73dca58e59d7a681`）。**产物门（逐项实测）**：干净产物 661 文件与 ZIP 661 条目**双向一一对应**、零 `demo_data`/`*.db`/`-wal`/`-shm`/`*.log`；ZIP 内 EXE 与干净产物 EXE **同哈希**（`fd47dc47…`）、绿色版 EXE 哈希不同（`0622ca39…`）→ 两个入口确实分开；两份 EXE 的 PE 产品版本 `3.15.0.0`（UTF-16-LE 检索）；包内散文件逐项确认携带本轮标记（商品列表含「查看专属价格」且无「默认价」「启用状态」、编辑页含「客户专属价」、开单页无「常规价」且保留 `repriceRowsForCustomer` 护栏、拿货 / 退拿货页无「常规价」与 `renderPriceQuick`、死模板 `customers/prices.html` 不在包内）。绿色版演示数据业务单据合计 **200**（orders 125 + 拿货 45 + 退拿货 30），`integrity_check` = ok，`customer_prices` 208 行，商品档案价非 0 的 **0** 条。**隔离冒烟（真实 EXE，非源码）**：绿色版从**副本**启动（交付产物的 `demo_data` 不被写脏），`/health` = `v3.15.0` 且 `data_root` 指向包内 `demo_data`，首页 + 四类开单 + 客户 / 商品 / 账款 / 设置 / 分析 / 回收站 **11 条路由全 200**；交付版用独立 `ERP_DATA_ROOT`/`ERP_CONFIG_PATH` 启动，`/health` = `v3.15.0`、5 条路由全 200、隔离库正常创建。**新取价口径在包内实测**（打真实 EXE 的 `/orders/api/products`）：演示客户 × 有专属价的商品 → `unit_price=52.00`、`is_customer_price=true`；同一客户 × 无专属价的商品 → `unit_price=null`（**不再回落任何档案价**）；真实 EXE 渲染的 `/orders/new` 无「常规价」、`/products/` 含「查看专属价格」且无「启用状态」「默认价」、`/products/1/edit` 含「客户专属价」。**正式数据根（`Documents\简单ERP数据`）冒烟前后 size / mtime / WAL 零变化**。**收尾**：验收探测留下的 `demo_data\data\erp.db-wal`/`-shm` 用读写连接 + `wal_checkpoint(TRUNCATE)` 清掉（现在只剩 `erp.db`，`integrity_check` 仍 ok）；旧 v3.14.0 三种本地产物已按「只留当前版本」清理。**本版已发版（2026-10-16）**：提交 `fa0b582` 推送 `main`（远端 HEAD 核实 = `fa0b5821`），annotated tag `v3.15.0`（tag 对象 `9a6e5048` 解引用回该提交），[GitHub Release v3.15.0](https://github.com/Astyyym/simple-erp/releases/tag/v3.15.0)（Latest）挂**两个资产**：`simple-erp-setup-v3.15.0.exe`（40,153,916 B，SHA256 `a0e1df76eea6f5efdaab5a034c7d336a358e8ab973fae7ca0f2a095b62f28e7e`）、`simple-erp-windows-v3.15.0.zip`（53,625,249 B，SHA256 `5d2e2e97200d0ff3a3a0c6d3f2a3bdccc527cf89052fe58c73dca58e59d7a681`）——`gh release download` 取回后与构建产物 `sha256sum` 相同且 `cmp` 字节一致；绿色版目录（另构建、自带 200 条演示数据）不入 Release；推送后远端树 **511 条目**（较 v3.14.0 减少即 68 个 `开发短计划/` 文件移出公开仓库）。安装包真实安装/升级/卸载链路与目标打印机实打仍未做。

- **文档与链接**：需求文档（§3 开单 / 客户与商品行 + §3.9 变更记录）+ 5 份技能 references + `memory/project-context.md` 同步新口径；另修 **8 处**「描述当前状态却还写着已删功能」的残留（`README.md` 3 处、需求文档商品字段、`库存成本与对账业务契约` §2.2 三行、`excel-data-export.md` 单据导出的「规格」列、`erp-session-notes.md` / `erp-ui-redesign.md` 的「客户价/常规价切换」、`多端演进与桌面端改造.md` 的失效查证行与 `(name, spec)` 身份）。`开发短计划/` 转为**本地私有**（`.gitignore`）后，README / docs / skills 里 **45 处**指向它的 markdown 链接改写为 `开发短计划/…` 路径引用（不再产生公开仓库死链），并在 `README.md` / `docs/README.md` / `AGENTS.md` 写明该目录不随公开仓库发布。

## v3.14.0 - 开单单价随客户走 + 退货来源候选放开成本门槛（2026-10-15，打包发版）

- **单价跟着客户走（真缺陷）**：销售 / 退货开单页（含重编辑）此前**只在「先选客户、再点商品」这一种顺序下取到客户历史价** —— `refineProductRow()` 仅在点选商品那一刻读一次 `customer_id`，页面上**没有任何**「往来对象变了就重算」的入口（全库搜不到客户框的 `change`/`input` 监听）。于是「先点商品后选客户」「已填行后换客户」「重编辑页换客户」三种顺序单价全部停在档案默认价（隔离库实测：同一商品默认价 100、甲成交 80、乙成交 85，三种场景都显示 100）。现由新增的 `ErpOrderEntry.onCounterpartyChanged()` 订阅往来对象变化，客户一变就按**新客户**重写所有已命中商品档案的行（有客户价取客户价，无则取档案默认价）；按用户决策**完全跟着客户走、不保留手输过的值**，行上仍可用「常规价」勾选项切回档案价。hidden 输入框被脚本赋值时 `input`/`change` 都不触发、`MutationObserver` 也监视不到 `value` 属性，因此该 helper 用「值指纹 + 200ms 轮询 + 自定义事件立即提交」三路兜底，覆盖候选点选 / 手输清空 / 关联原单带出三条赋值路径；另加行级请求序号守卫，防快速换客户时旧响应覆盖新结果。
- **退货来源候选放开成本门槛（用户口径）**：候选此前只列「明细带成本快照」的销售单，缺成本的旧单整单进不了候选、只报一个「另有 N 张」张数。这道门槛自 v3.1.0 就在，而 2026-10-13 起原单关联已降级为**只作追溯、不改变计价** —— 一个纯追溯功能被成本分摊的前置条件把关，门店表现为「想给旧单留个关联线索却选不了」。现**全部有效销售单都进候选**，每张附 `cost_ready`（明细是否全部带成本），缺成本的标注「**无成本，仅追溯**」；创建与重编辑两条路径同步按 `cost_ready` 分流：成本不齐备时强制走手输退货路径（否则来源分摊会以「原销售明细缺少可靠成本」整单拒掉 → 变成「能选不能存」，草稿更会被锁成「能存不能改」的死路），`source_order_id` 仍记在单上保追溯；**成本齐备者的逐行分摊（可退额度 + 按原单成本回冲）行为一字未变**。口径判定收口为单一常量 `RETURN_SOURCE_COST_READY_SQL`，候选标注 / 创建分流 / 重编辑分流三处共用，防漂移。预览与提示文案按两种关联语义分开表述，不再一律写「不影响计价规则」。
- **拿货 / 退拿货页不再误用客户价**：两页的单价快捷此前借用 `/orders/api/products?customer_id=…`，而该参数语义是**客户**、拿货页同名字段存的是**供应商**（客户与供应商共用 `customers` 档案），于是把「这家客户当初买多少钱」当成拿货单价候选。现在单价语义回归入库成本，快捷只留「常规价（= 商品档案零售价，随目录内嵌下发）」，不再借用订单接口。
- **顺带修掉既存缺陷**：重编辑页 `{{item.source_item_id if item else ''}}` 只判「有没有明细」，导致 NULL 被 Jinja 渲染成字符串 `"None"` —— 前端把普通销售行误判成「原单带入行」，提交时还会把 `"None"` 当明细 id 送 `int()`；改为 `or ''`。
- **本轮验收**：全量 pytest **998 passed / 16 skipped / 0 failed**；前端 Node **106 passed**；三份开单模板内联 JS `node --check` 全 OK。隔离库真实浏览器（headless Chrome + CDP）实测：先点商品后选客户 → 80、再换客户 → 85、多行同步跟随、重编辑页换客户 80→85、常规价仍可切回 100、退货手输行换客户 100→85；拿货 / 退拿货页只剩「常规价」无客户价；**关联缺成本旧单可正常带入并保存**（明细不绑来源、按手输路径落库）；**关联成本齐备单仍按原成本回冲**并产生库存流水，且其带入行不随换客户被重算（承诺按原成交价退）。**改断言不删测试**：退货来源候选、历史成本补录、拿货页价格快捷三处旧口径断言按新契约改写。**未验**：WebView2 桌面壳内重跑（本轮在 headless Chrome 验）、目标打印机实打（本轮不涉及打印版式）。
- **产物与发版证据（2026-10-15 发布）**：提交 `468a8f0` 推送 `main`（远端 HEAD 核实 = `468a8f06`，本次一并带上 v3.13.0 提交 `2668923` 与 5 个 docs 回填），annotated tag `v3.14.0`（tag 对象 `ed6a11e8` 解引用回该提交），[GitHub Release v3.14.0](https://github.com/Astyyym/simple-erp/releases/tag/v3.14.0)（Latest）挂**两个资产**：`simple-erp-setup-v3.14.0.exe`（40,151,740 B，SHA256 `a604b1065f9944fb05320565fa8f6f820fa3bb61362ebf1f4d9f6a4af8763430`）、`simple-erp-windows-v3.14.0.zip`（53,627,210 B，SHA256 `2be0460bcd4074b68f52aa8f5775b7f0a14c7381741b771c6eef7c7e6f1466a9`）——两资产 `gh release download` 取回后与构建产物 `sha256sum` 相同且 `cmp` 字节一致；绿色版目录（另构建、自带 200 条演示数据）不入 Release。**产物门**：ZIP 内 EXE 与干净产物 EXE 同哈希（`e31f06bc…`）、绿色版 EXE 哈希不同（`304a0af7…`）→ 两个入口确实分开；安装包源目录与 ZIP 零 `demo_data`/`*.db`/`-wal`/`*.log`（ZIP 662 条目 = 干净产物 662 文件，双向对应）；两份 EXE 的 PE 产品版本 `3.14.0.0`（UTF-16-LE 检索）；包内散文件确认携带本轮标记（`order-entry-common.js` 含 `onCounterpartyChanged`、`orders/new.html` 含 `repriceRowsForCustomer`、拿货页含 `renderPriceQuick`；`routes/orders.py` 编进 EXE 内嵌 PYZ，其后端语义由冒烟实测覆盖）；绿色版 `demo_data` 业务单据合计 **200**（orders 125 + 拿货 45 + 退拿货 30），`integrity_check`=ok，`customer_prices` 204 行。**隔离冒烟（交付版 EXE，非源码）**：`ERP_PORT=5099` + 独立 `ERP_DATA_ROOT`/`ERP_CONFIG_PATH`，`/health` = `v3.14.0`、`database`=ok，首页 + 四类开单 + 客户/商品/账款/设置/单据/分析/回收站共 11 条路由**全 200**；包内实测同一商品按客户取价三档分流（客户1=52.00 / 客户3=57.02 / 无客户=56.00）、缺成本快照老单 `cost_ready=false` 仍进退货来源候选。**正式数据根（9 文件）冒烟前后 size/mtime 零变化**。安装包真实安装/升级/卸载链路未做。旧 v3.13.0 三种产物已清理（`dist` 只留当前版本）。

## v3.13.0 - 对账收口、对账单版面收缩与设置页三版面样张预览（2026-10-15，本地打包；未打 tag、未建 Release）

- **删除独立的「打印账款流水 PDF」**：用户决策——它和往来对账重复。其独有内容（收款 / 调整 / 余额）并入往来交易对账单；`/accounts/ledger_pdf` 路由、模板与生成器一并移除。
- **往来对账整体挪进账款管理（归属根修）**：路由 `/analytics/reconciliation` → `/accounts/reconciliation`，侧栏高亮与「返回账款管理」恒定；删掉 2026-10-10 那套 `from=accounts` 来源覆盖机制（实测有三个漏点：勾选后、全选 POST、桌面预览返回都会把归属叛成数据分析）。
- **对账单版面重做（2026-10-14 定方向、2026-10-15 收缩）**：①「整单交易总额」独立竖列 + 整列加深，**金额只在「日期 · 单号 · 类型」那一行印一次**，商品行留空单元格保住整列底色；② 表格下方改为**四类各一个矩形框**（上侧类型、下方金额）+ **居中三行**：资金流水合计（收款 − + 调整 ±）、流水总额（四类净额）、汇总（前两者相加；默认全选时等于台账区间净变动）；③ 逐笔资金明细、四格余额卡片、口径说明小字与免责声明**不再上纸**。实测表格下方整块 **149.4mm → 72.5mm**，主表 6 张单的对账单从 **2 页降到 1 页**，且版面不再随收款笔数增长（原先每多一笔资金流水 +6.7mm）。
- **设置页新增「打印版面预览」**：三个版面（四类开单 241mm 连续纸 / 客户货款汇总表 A4 / 往来交易对账单 A4）各给「预览打印效果」与「生成预览 PDF」，并到同一张表里（原来底部动作条那两个预览按钮并入此表，同一件事不再有两个入口）。样张全部是**固定示例数据**、不读业务库（新增 `settings_summary_preview_context` / `settings_reconciliation_preview_context`，货款汇总的行分组元数据复用真实导出用的函数，避免样张与线上口径漂移）；对账单样张数字自洽（281.00 − 200.00 = 81.00）。桌面壳里三个样张仍走统一预览页且**预览页不预渲染 PDF**（补测试钉住）。
- **顺带修掉**：对账页四类总额行把负向类型印成「客户退货（−）¥-20.00」双符号——方向已由标签承担，数值一律取绝对值。
- **本轮验收**：全量 pytest **991 passed / 16 skipped / 0 failed**（含新增「三个样张绝不读业务库」哨兵测试）；隔离数据根真实 HTTP 实测设置页 6 个入口、三张样张 PDF 页框（连续纸逻辑页 + 两张 A4 竖版）；对账单真实渲染 40 行跨页 4 页不崩、pdftotext 验纸面内容与白名单。**未验**：WebView2 桌面壳内点击链路、目标打印机实打（A4 对账单纸面位置）。
- 产物（本地，2026-10-15 **12:13 重打包**实测；同日 10:28 那份已被覆盖）：`dist\local-v3.13.0\简单ERP\`（**665 文件，自带 200 条演示数据，打开即用** —— 销售 90 + 退货 35 = `orders` 125，拿货 45，退拿货 30，合计 **200**；回收站里另有订单 3 / 拿货 1 / 退拿货 1，`integrity_check` = ok，包根配置是「演示消防器材店」）、`dist\simple-erp-setup-v3.13.0.exe`（40,519,528 B，SHA256 `2c6dfba651bbb0187ccbc27fc3ae8ea99401b965a2a88009e73d8ab3a3742dd9`）、`dist\simple-erp-windows-v3.13.0.zip`（54,116,499 B，**663 条目**，SHA256 `cc59083616f9d2ce6ea99af79ce921659d91d3cc269f3e1c9adcf9abe4277459`）。
  **本轮重打包的原因**：出厂模板 `packaging\default_config.json` 补了 `print_info_prompt_dismissed`（提交 `f6e48fb`），必须让包内配置与模板一致。**内容门（逐项实测）**：ZIP 内 `简单ERP/config.json` 与 `_internal/config.json`、以及干净产物同名两份，均与 `packaging\default_config.json` **字节完全相同**（29 键，SHA256 `d40f2c4a…`）；ZIP 与干净构建目录**逐一对应（663 / 663，双向差集为空）**，无 `.db` / `demo_data` / `.log`；ZIP 内 EXE 与干净产物 EXE **同哈希**（`46ce9c00…`），绿色版 EXE 哈希不同（`4ab2c1cf…`）→ 两个入口确实分开；两份 EXE 的 PE 数字产品版本均为 `3.13.0.0`。
  隔离冒烟（复制到 ASCII 临时目录、原生路径启动）：交付版用隔离 `ERP_DATA_ROOT`，`/health` = `v3.13.0`、`database` = ok，`/`、`/orders/`、`/orders/new`、`/settings/`、`/accounts/reconciliation` **均 200**；绿色版 `/health` = `v3.13.0` 且 `data_root` 指向**包内 `demo_data`**，同样 5 条路由 **均 200**。**正式数据根（`Documents\简单ERP数据`）size / mtime / WAL 前后完全未变**（208,896 B，2026-10-14 22:29:38，无 wal/shm）。
  绿色版 `demo_data` 里由验收探测产生的 `-wal`/`-shm` 已按规矩用**读写连接 + `wal_checkpoint(TRUNCATE)`** 清掉（收尾后只剩 `erp.db`）；旧 v3.12.0 产物已清理。
  **本轮当时只做本地打包：未推送、未打 tag、未建 Release。** 2026-10-15 提交 `2668923` 随 v3.14.0 一并推送 `main`（远端可达，**仍无独立 tag / Release**，按仓库惯例只在发布版本挂 Release）；v3.13.0 三种本地产物已按「只留当前版本」清理，上记哈希为清理前实测留存。安装包真实安装/升级/卸载验证与目标打印机实打仍未做。

## v3.12.0 - 关联带入可替换、检索串归一、档案筛选与流水展开、编辑页主次重构（2026-10-14，打包发版）

- **换掉关联原单后，上一张带入的商品行不再残留（真 bug）**：`createPreviewApply()` 只有「插入」没有「撤回」，换单只 `reset()` 放开按钮、一行不动 → 旧单的行留在表里，新单再带入变成**增量叠加**（A 带入 3 行 → 换 B → 带入 B 变成 4 行）。现给带入加行级记录（`rows()` / `modifiedRows()` / `clear()`）：换单时**只撤自己插入的行**，用户手输的行一行不动；有手工改动先 `confirm` 一次，没改过静默移除。退货单 / 退拿货单共用。
- **检索串归一：输入法隔音符号 / 空格 / 全角（真 bug）**：中文输入法会在音节间插隔音符号，输入框里实际是 `xiao'fang`，而派生列是无分隔符形态 → `%xiao'fang%` 永远命中不了（`xf` 命中、`xiaofang` 搜不到）。现检索串先 NFKC 归一再去隔音符号 / 空格，5 处裸 `LIKE` 一并收口；实测 `xiao'fang` 命中 6 个消防商品。
- **商品管理恢复「全部 / 待补全 / 按拼音排序」**：v14 删 `spec` 列时把商品侧档案筛选整行一起删掉（判定恒为真），客户页却仍有 → 两页不一致。现按与客户侧同构的口径（开单自动建档 + 品牌为空）在批量动作条恢复三态 chip 与拼音排序（`products` 路由加 `sort` 白名单 + `pinyin_full`）；`.list-facet` 整类删除，客户页同步搬进动作条。
- **导入字段不完整不再拦行，改为标「待补全」**：商品缺品牌**或**缺默认价都算待补全；导入时默认价可留空（按 0 落库）不再拦行；提醒三处齐（预检页提醒条 + 每行说明、导入摘要条数、列表徽标与筛选）；客户 / 商品两页模板新增第二个 sheet「填写说明」（第一行仍必须是纯表头）。
- **账款流水可展开单据明细**：流水行新增「展开 / 收起」，行下插入该单商品明细（新增 `/accounts/api/ledger_items`），`grid-template-rows` 过渡约 250ms，展开时**不回顶、不闪烁**（实测被点行 `top` 恒 573.48、滚动恒 0）。
- **商品 / 客户编辑页「主次与分组」重构**：商品页实测内容高 1457px（首屏 810px），「保存」按钮落在首屏之外；只读库存卡压在可编辑内容之上；`col-6` / `col-8` 三种右边缘；库存期初 368px 常驻展开。改为**主栏 + 320px 侧栏**（用户确认的编辑页范式）：商品页 1457 → **1013px**（「保存」首屏可见；图片进侧栏、只读块回主栏铺 5 列、期初默认展开）、客户页 579 → **709px**；「待补全」从 57px 横幅改为页头 chip + 字段 ⚠（判定口径仍走 `master_data_quality`，新增 `product_missing_keys` / `product_missing_summary` 两个同源派生函数）；右边缘 3 种收敛为 2 种。
- **本轮验收**：全量 pytest **1006 passed / 16 skipped / 0 failed**；定向 93 passed；前端 Node **101 passed**；隔离实例真实浏览器（headless Chrome + CDP 原生鼠标事件）实测——换单后行数归零、再带入只 1 行，`xiao'fang` 命中，chip 三态坐标逐像素不变（商品 / 客户均 `1140/1196/1308,403`），流水展开几何与动画达标；导入全流程走完（上传 → 预检 → 确认 → 列表）「待补全」计数与徽标一致。**未验**：WebView2 桌面壳内重跑（本轮在 headless Chrome 验）、目标打印机实打（本版不涉及打印）。
- 产物：`dist\local-v3.12.0\简单ERP\`（**自带 200 条演示数据，打开即用**）、`dist\simple-erp-setup-v3.12.0.exe`、`dist\simple-erp-windows-v3.12.0.zip`（安装包与 ZIP **不含**演示数据；per-user 安装不变）。已发版：提交 `b5df1ee` 推送 `main`、tag `v3.12.0`、[GitHub Release](https://github.com/Astyyym/simple-erp/releases/tag/v3.12.0) 双资产（`state=uploaded`，下载回本地 SHA256 与构建产物一致）；旧 v3.11.2 产物已清理。

## v3.11.2 - 带入一次性、关联单据改选与拼音全拼（2026-10-14，本地打包供验收；未打 tag、未建 Release）

- **「按预览带入商品行」点几次插几次（真 bug）**：`previewApply` 每次点击都「写 `tbody.rows[0]` + 追加 N-1 行」→ 点两次插两套重复行（数量/金额/成本翻倍），且无条件复用第 0 行会静默覆盖用户已手输的那一行。现改为共享的**一次性带入** `ErpOrderEntry.createPreviewApply`（退货单 / 退拿货单共用）：只复用**空行**，带入一次后按钮置灰为「已带入商品行」，**只有换成别的原单**（或点「清除关联」）才重新放开。
- **选完原单后点输入框只列出已选那一张（真 bug）**：`focus` 按输入框当前值（=已选标签）查询只命中它自己，而「刚选过 → 空查询列全部候选」只写在 `click` 分支且带 `isOpen()` 早退；真人点击 `mousedown→focus→mouseup→click` 里 focus 的请求通常先回来 → **第一次点击**只看到 1 条（接口慢时又正常，表现为时好时坏）。现 `focus` 与 `click` 共用 `queryOnOpen()`（仍只对两个来源框生效，客户/商品框行为不变）。
- **改了字但没从候选里选，关联与预览仍是旧单（真 bug）**：来源框接入 `onInputChange`，输入内容与当前关联不一致时**立即解除关联**（清隐藏域 + 收起预览 + 清行上来源明细）——此前会把旧单号当来源提交（同客户的不同单还会静默关联错单）。
- **拼音从「只有首字母」扩到「首字母 + 整串全拼」**：新增派生列 `products/customers.pinyin_full`（整串小写全拼，幂等加列 + 只补空值回填、**不升 schema 版本**）；匹配统一走 `utils/pinyin.py` 的 `name_match_clause()` / `name_match_params()`，**17 处匹配点**（商品/客户列表与建议、开单联想、拿货对象、两张原单候选、单据管理与导出的客户筛选、货款汇总）三列同参，**筛选框也按全拼匹配**（推翻 v3.10.0「三处纯筛选条件保持按名称匹配」的口径）。
- **本轮验收**：全量 pytest **965 passed / 16 skipped / 0 failed**、前端 Node **98 passed**；7 个页面 32 段内联脚本 `node --check` 全 OK；隔离实例真实 HTTP 逐接口命中拼音（含两张原单候选与单据筛选框）；**真实浏览器（headless Chrome + CDP 原生鼠标事件）两页实测**：第二次带入行数不变、按钮置灰、选完后点一次输入框即列全部候选（3/3、4/4）、改字后隐藏域清空且预览隐藏。**未验**：WebView2 桌面壳内重跑（前端行为已在真实 Chromium 内核上验证）、目标打印机实打（本版不涉及打印）。
- 产物：`dist\local-v3.11.2\简单ERP\`（自带 200 条演示数据）、`dist\simple-erp-setup-v3.11.2.exe`、`dist\simple-erp-windows-v3.11.2.zip`（三种形态同源，per-user 安装不变）。

## v3.11.1 - 开单四项缺陷修复 + 每联打印行数可配置（2026-10-14，本地打包供验收；未打 tag、未建 Release）

- **销售单 / 退货单点选商品后下拉不关、单位与单价不带入**：根因是 `applyProductToRow()` 里还留着单位双控件时代的残留变量（P2 已删），点选候选时抛 `ReferenceError`，连带跳过「关闭候选浮层 / 写单价 / 合计重算」——一个错误造成三个现象。拿货 / 退拿货本来正常。已修并补四页共用回归测试（`tests/frontend/order_entry_product_pick.test.mjs`）。
- **关联原单后再也改不了**：退货单的原销售单输入框只在 `focus` 时打开候选，而点选候选后焦点仍留在输入框，再点不会触发 focus → 候选再也打不开。现在「再点一次输入框」会按空查询重新列出全部候选；并新增**「清除关联」**按钮（清输入、隐藏域、预览与行上来源明细）。
- **关联原单不带出往来对象**：退拿货单原先完全没有这段逻辑；退货单只在「客户框为空」时带出。现在两页共用同一份对齐逻辑，**以原单为准**（不同则改为原单对象并说明原因），避免用户带着一个注定被拒的对象去保存。
- **退拿货单的原拿货单改成可检索输入框**：与退货单同款（点击列最近单据、可手输单号 / 往来对象 / 拼音过滤、可改选、可清除），不再是服务端渲染的 200 条原生下拉；候选接口 `/purchases/api/purchase_sources` 支持 `?q=` 并带出 `customer_id`。
- **深色模式下单据管理的退货单行看不清**：Bootstrap 的 `.table-danger` 是浅色模式写死的变体，它的条纹 `box-shadow: inset …` 会盖在行背景之上，只覆盖 `background/color` 不够。现在深色 / 跟随系统下把该覆盖层变量一起置空，并补了悬停态（原先悬停也会变浅底浅字）。
- **每联打印行数可配置（6 / 8 / 10 / 12，默认 10）**：设置页「打印位置校准」新增档位选择，四类开单打印与三份录入页的换页提示同源（配置项 `slip_size`）。行高靠压单元格内边距换来（≤10 行 0.9mm、12 行 0.4mm），**字号层级一字未动**；新增逐档行几何契约测试（一联恰好装 N 行、N+1 行换第二联、满档位仍容得下一行品名折行）。档位越界 / 配置写坏一律回落默认 10。**目标打印机实打未做**。

## v3.11.0 - 开单四项体验修复（2026-10-13，本地打包供验收；未打 tag、未建 Release）

- **退货单 / 退拿货单的原单关联**：改为可搜索的检索框（点击列出最近单据，可手输过滤），选中后显示原单预览与「一键带入商品行」；**关联只作开单参考与追溯**，不再要求每一行都对上原单 —— 带入后又新增手输行也能正常保存。
- **单位列**：四类开单页统一为单控件（输入框 + 候选浮层），不再并排「下拉 + 输入框」；列宽 190 / 96 → 统一 110px；本页手输的新单位跨行共享。
- **商品列**：四类开单页统一为手输 + 联想（拿货 / 退拿货仍**必须命中已建档商品**，未命中会提示去商品管理建档，不会自动建档）。
- **修掉几处真实缺陷**：新增行（克隆行）的联想与单位候选绑不上、会串用上一行的浮层；退货单详情页的「原单已删除」误显示；拿货 / 退拿货手输未命中时冒出技术性报错。
- 不含：单据管理筛选区重排（P5）、每联打印行数可配置（P6）—— 这两项未做，不在本包范围内。

## v3.10.0 - 单位落库统一、单位记忆与联想下拉去 datalist（2026-10-13，打包）

- **单位落库语义统一（本版主线，行为变化）**：四类开单页原先对「单位」的处理互不一致，本版一次性统一。
  - **修掉「选中已有商品时提交的单位被静默丢弃」**：`_select_existing_product` 原先直接返回 `products.unit`，于是门店最常见的动作「输入商品名 → 命中 → 改单位」**改了等于没改，且无任何提示**。现改为**以提交的单位为准**（提交空值则回落商品档案单位），同时写本单明细并同步 `products.unit`；与「手输新名称」路径（本来就采纳）**行为完全一致**。
  - **拿货/退拿货页单位改为可编辑**：原先单位列是**只读文字**、全页 `name="unit"` 出现 **0 次**，提交的单位被直接无视。现改为可编辑控件（下拉 + 自由输入），只作用于**本单明细**，**不回写商品档案**（拿货商品必然已启用库存，锁定规则不允许）。
  - **来源带入的单位默认值**：退货单从销售单带入、退拿货从拿货单带入时，明细单位**优先沿用源单快照**，同时**允许用户修改**（仅改本单明细）。
  - **不改动的部分**：商品编辑页对已启用库存商品**仍锁定单位**（报「单位已锁定」）；开单页不校验锁定是**有意设计**（门店效率优先），需求文档已记载，不得当缺陷修回。
  - **不做数量/成本换算**：库存状态无单位列，100「个」改叫 100「箱」账面数字不变。
- **单位候选池（零迁移）**：四页单位下拉的候选 = **默认三项（个/套/件） ∪ `products` ∪ `order_items` ∪ `purchase_order_items` ∪ `purchase_return_items` 的已有单位**。本页新输入的单位**立即进入本行候选**，无需等保存；跨单记忆靠四表汇总，**不新建表、不升 schema 版本**。默认三项始终排在最前。
- **联想下拉全部改为自有浮层（去 `datalist`）**：原生 `<datalist>` 在 WebView2 桌面壳里**候选弹层不绘制**（同宿主对照实验：选项在 DOM 里可解析、输入框聚焦，但一个像素都不画）。本版把客户/商品/单据管理三张列表页与开单页的联想**全部换成应用自有的 `popover` 浮层**，并在真实 WebView2 窗口里实测「确实画出来了」。同时修掉「滚动 1px 就关闭联想框」的抓取式滚动监听。
- **客户与商品双向拼音联想**：客户侧新增 `customers.pinyin_initials`（幂等加列 + 只补空值的回填，不升 schema 版本），**5 个写入点**全覆盖（导入新建、编辑改名、开单自动建档、拿货自动建档、服务层新建）；**8 处匹配点**加拼音（客户列表搜索/建议接口、单据管理客户查询、拿货对象查询、货款汇总客户查找）。商品侧两处建议接口补拼音。**三处纯筛选条件保持按名称匹配**（用户在筛选框输完整名称，不是选词），并用测试钉住不被误扩大。
- **商品身份合并（型号并入名称）**：`spec` 并入 `name`（直接拼接、不加分隔符），四张明细快照表的 `spec` 列删除，schema 13 → 14；导入导出同步去型号、导入模板不再接受「型号」列。**门店库升级前必须先跑 `tools/precheck_product_identity_merge.py`**，撞名即中止交用户裁决，绝不静默合并。
- **开单页表头深色背景、录入页换页提醒行**：四类开单表头改为实色深底（实测对比度浅色 10.35 / 深色 10.46，含无 blur 回退路径）；录入页按单联容量插入「本张已满 N 行，续打在第 M 张」提示行（提示行不含表单控件、不参与提交）。
- **本轮验收**：全量 pytest 前台 **938 passed / 0 skipped / 0 failed**、前端 Node **82 passed**；四页渲染后内联 JS `node --check` 全 OK。真实隔离服务 + 浏览器：四页候选池一致、下拉↔输入框联动、手输即时追加、选中商品带入档案单位、**跨单记忆重开仍在**。**真实桌面壳（pywebview + WebView2）两路证据**：销售页与拿货页的单位列均为**可编辑输入框 + 下拉框**（非灰色只读文字），联想浮层确实绘制。
- 产物：`dist\local-v3.10.0\简单ERP\`、`dist\simple-erp-setup-v3.10.0.exe`、`dist\simple-erp-windows-v3.10.0.zip`（三种形态同源，per-user 安装不变）。

## v3.9.1 - 桌面壳端口可从配置/环境变量解析（2026-10-12，打包）

- **修掉「桌面壳端口写死 5000」**：端口原先在 `desktop_app.py` 的 `APP_URL` / `_run_server` / `_port_is_open` **三处硬编码 5000**，既不读 `config.json` 的 `port` 也不读 `ERP_PORT` 环境变量。后果是**无法在别的端口跑隔离实例**——发版冒烟时设了 5099，EXE 照样起在 5000，只能让 5000 空着等它，多实例/预览/并行验收都做不到。现在新增 `resolve_port()`：`ERP_PORT` > `config.json` 的 `port` > 5000（与源码入口 `app/app.py` 同一口径），`_app_url()` 用解析结果拼地址，`main()` 把端口**解析一次并贯穿**探测/绑定/加载地址（否则探测的是一个端口、绑的是另一个，会误判「已在运行」）。
- **host 仍固定 `127.0.0.1`，不从配置读**：产品永久免登录，绝不能因为 `config.json` 里写了 `0.0.0.0` 就把服务暴露到局域网/公网。有测试钉死这条（配置里写 `0.0.0.0` 时地址仍是回环）。
- **端口解析失败不阻止启动**：非法值（空/非数字/越界/小数）与配置读不到都回退 5000，不抛异常——端口问题不该让桌面壳起不来。
- **保持既有契约**：`_port_is_open()` 仍是**零参可调用**（既有启动测试用零参 lambda 替换它），显式传入的 `port` 优先于解析结果。
- 本轮验收：新增 `tests/test_desktop_port.py` **17 passed**（改动前 RED 确认 16 failed）；桌面/设置相关 **45 passed**；全量 pytest **893 passed / 0 skipped / 0 failed**；绿色版 EXE 端到端实测 `ERP_PORT=5099`（配置里写 5000）→ `/health` 在 **5099** 就绪、**5000 无监听**。
- 产物：`dist\local-v3.9.1\简单ERP\`、`dist\simple-erp-setup-v3.9.1.exe`、`dist\simple-erp-windows-v3.9.1.zip`。

## v3.9.0 - 历史成本人工补录（销售单）（2026-10-12，打包）

- **新增「人工补录历史成本」**：零建档/先卖后补路径下的历史销售单，成本快照为空（无库存状态行 → 成本恒空），于是毛利算不出、成本诊断显示「未覆盖」、退货来源下拉选不到、关联退货被硬拒——唯一现成办法是「作废重录」，单号会变、打印要重打。本版给这类老单第二条纠错路径：**选一张老单、人工填成本、系统补上快照并留痕**。
- **核心取舍：只补利润、不动库存。** 补录只写 `order_items` 的三个成本/来源字段与一条审计；`inventory_postings`、`product_inventory_state`、`inventory_initializations` **三表逐行不变**（测试与隔离验收均有硬断言）。这段毛利是人工填的，界面明确标记「人工补录」，不冒充真实销售时快照。
- **新增 `order_items.cost_source` 标记**：`NULL` = 系统写入的销售时真实快照（**不可覆盖**）；`'backfill'` = 人工补录（**可再修正**）。幂等加列，**不推进 `CURRENT_SCHEMA_VERSION`**（仍 13），对齐既有 `origin`/`brand`/期初 `cost_source` 惯例；旧数据留空即按真实快照处理。
- **补录范围与保护**：只限销售单且状态为 `saved`/`printed`（草稿、作废、退货单、拿货单各有中文原因）；逐行补录，可按**单位成本**或**总成本**（反推单位成本）填写，非负、有限、≤6 位小数；**原因必填**并写审计（与补录写入同一事务，避免「补录成功、审计丢失」）；**已有有效退货的单拒绝再修正**——原行成本改了、退货累计成本不会重算，已全部退完的单会留下永久差额（保守拒绝，用户 2026-10-11 拍板不放宽为「允许+警告」）。
- **补录后该单禁止重编辑**：重编辑会重建明细行、无声清掉补录数据。列表/当天历史/详情三处「编辑」按钮置灰并给出原因，**编辑路由 GET 与 POST 双重拦截**（不可绕过）。**补录页在提交前就披露**这条限制——补录前该单恰恰是可编辑的，提交后才失去编辑能力。
- **发现路径**：单据管理列表给缺成本销售单加「缺成本」徽标 + 「只看缺成本」筛选（`cost=missing`，勾选后非销售分支固定不显示，语义写死）+ 已选条件 chip。详情页新增「补录历史成本」按钮、成本来源列与补录成功提示；含补录行时毛利文案由「按销售时成本快照计算」改为「含人工补录成本」。
- **零改聚合**：分析/利润服务本就按 `cost_total_micro IS NULL` 判定未知成本，**补录数据落库即自动生效**。连锁效果：补录后该单进入退货来源下拉，可按补录成本参与关联退货（商品未启用库存时退货仍被拒绝）；分析页该单离开「未覆盖」范围，毛利参与计算。
- **顺带修掉 v3.8.0 遗留回归**：`tests/test_installer_packaging.py` 两条契约测试仍按 UTF-8 读已改成 GBK 的 `打包Windows安装包.bat` → `UnicodeDecodeError`、全量 2 failed。改为统一走 `_build_script_text()`（GBK 解码）后通过。脚本内容本身完好。
- **新增 `.bat` 行尾契约测试与根因更正**：受控实验（真实 `cmd.exe`，2×2 对照）确认**决定因素是行尾、不是编码**——UTF-8+CRLF 与 GBK+CRLF 均正常，LF 一律跳过。因此**没有**把 `setup.bat`/`打包Windows桌面版.bat` 转 GBK（此前打算建立在被推翻的说法上，已取消）。新增 `test_all_batch_scripts_have_crlf_line_endings` 对全部 `*.bat` 断言 CRLF 且无裸 LF（用 `read_bytes` 数字节，避免 normalize 误判）。
- 本轮验收：全量 pytest **876 passed / 0 skipped / 0 failed**（v3.8.0 基线 855 + 新增 21）、前端 Node **57 passed**；新增 `tests/test_cost_backfill.py` 20 条行为测试（RED 已确认 19 failed）。隔离端到端真实 HTTP 页面走通：零建档缺成本 → 分析「未覆盖」→ 列表「缺成本」徽标 → `cost=missing` 筛选 → 补录页双披露 → POST 补录 → 详情页「人工补录」+「含人工补录成本」→ 落库 60.00 元/120.00 元/`backfill` → **库存三表逐行一致、流水 0 条不变** → 分析毛利 8000 分 → 退货来源下拉 0 条 → 1 条 → 编辑入口 HTTP 400。**用户侧人工验收（2026-10-12，已确认完成）**：真实门店数据上的补录流程已走通；目标打印机实打（10 行单 / 11 行单）已完成——**PDF 分联正确且纸面位置与字迹已实机确认**。用户于 2026-10-12 确认其余遗留验收项全部通过；「非中文 Windows / 无 WebView2 / 中文用户目录路径」按用户产品范围判断关闭（实际场景为中文 Windows + 已装 WebView2）。
- 产物：`dist\local-v3.9.0\简单ERP\`、`dist\simple-erp-setup-v3.9.0.exe`、`dist\simple-erp-windows-v3.9.0.zip`（三种形态同源，per-user 安装不变）。

## v3.8.0 - 单联 10 行续打、开单行工具条、一键清空数据与打印渲染提速（2026-10-11，打包）

- **四类开单单联容量 8 → 10 行（压内边距，字号不动）**：单元格内边距 `1.6mm 1.2mm` → **`0.9mm 1.2mm`**，**字号层级一字未动**（保留上一版 24/16/15/14/13/12px 的可读性修复）。明细按 `items | batch(10)` 分块，每块自成一张 `<table>`（含 `colgroup`/`thead`/`tbody`），列宽按新版心重算合计 **201.3mm**（`13 / 74.3 / 16 / 24 / 35 / 39`）。**实测分联**：1/9/10 行 → 1 联，11/20 → 2 联，21 → 3 联，31 → 4 联；序号跨联连续（1..10 / 11..20 / 21..31）；每联左右版心恒 7.3mm、表头重复。**可读性优先于行数**：超长品名（约 18 字以上折行）会自动溢到下一联，属排版物理限制，不作为缺陷。
- **续打提示与合计口径**：非末联给「本张合计」（该张 10 行小计之和）+ 提示行「本张已满 10 行；因行数限制，以下内容续打在第 N 张」并**强制换联**；末联给「本单合计」+ 页脚。修掉旧版「9~11 行第 2 联只剩页脚」「12 行以上第 1 联连合计都被拆走」两个缺陷。合计口径经用户确认「每页都合计」——不采用「每张都印整单总额」（会让第 1 张的合计与它上面 10 行对不上）。
- **四类开单表格下方新增行工具条**：`[新增一行] [删除一行] [一键清空]` 随表横向滚动、左缘对齐「产品名称」列；新增 `removeRow()`（删最后一行，仅剩 1 行时按钮 disabled）；右侧动作区只留「保存 / 保存并打印」。
- **设置页新增「清空所有数据」**：清空全部业务数据（单据/明细、往来对象、商品与库存、账款、对账快照、审计）与 `product_images/`、`temp_pdf/`、`logs/` 内容并**回出厂设置**；**保留** `backups/`、`legacy_archive/`、数据目录路径记忆与库文件本身（清表不删库）。安全设计：执行前自动 `pre_purge` 备份 → 必须输入确认词「清空」→ 单事务执行、失败整体回滚 → 完成后提示完全退出重开；审计同时写入 `logs/purge.log`（因 `audit_logs` 会被同批清掉）。**新增 `order_number_sequences` 表保留当日 MD 流水高位**——清空后 `orders` 为空，仅靠现算会与已打出的纸质单撞号；`next_order_no` 在实时最大值与保留高位之间取大，正常开单口径不变。此为本批唯一超出「删表」范围的设计。
- **「保存并打印」不再渲染两遍**：桌面预览页原先渲染 PDF 一遍、iframe 再请求一遍（实测一次点击 `generate_order_pdf` 调用 **2 次**，合计约 1.9s）。改为预览页只渲染工具栏、PDF 交给 iframe 请求一次；启动后台线程预热 WeasyPrint 导入（把首次约 0.75s 挪到开机）。实测计数 **2 → 1**、一次点击 **0.822s**（原 1.9s）。
- 本轮验收：全量 pytest **855 passed / 0 skipped**、前端 Node **57 passed**；新增 `test_pdf_render_once.py`（8）、`test_purge_all_data.py`（10）等契约测试。隔离库实测：16 张业务表全清、`backups/`+`legacy_archive/` 保留、配置回出厂、MD/NH 单号续排（`MD202610110007` → 清空后 `...0008`）。**目标打印机实打（10 行单 / 11 行单各一张）已完成（用户侧人工验收，2026-10-12 确认）**——**PDF 分联正确且纸面位置与字迹已实机确认**。
- 产物：`dist\local-v3.8.0\简单ERP\`、`dist\simple-erp-setup-v3.8.0.exe`、`dist\simple-erp-windows-v3.8.0.zip`（三种形态同源，per-user 安装不变）。

## v3.7.0 - 四类开单连续纸打印：版式回归与字号修复（2026-10-11，打包）

- **字号放大（用户实拍反馈的直接修复）**：门店旧软件（秒账）打出的销售单与简单ERP 打印件实拍对比，**我们的字明显偏小、远距离核对看不清**（正文高度中位数 21px vs 16px，约 0.76）。根因是打印模板自身把字号定小且没有层级：表头/正文/合计 11px、备注 9.5px。本版一次性全改：h1 公司名 24px、单据名 16px、合计 15px、表头+正文 14px、页脚 13px、备注 12px，单元格内边距 0.8mm → 1.6mm 1.2mm。实测正文字框高 **3.84mm → 4.89mm**（+27%），与秒账实拍水平相当。**可读性优先于行数**：长单由用户拆单打印，自动跨页只是兜底，不为塞进一联而压缩字号。
- **连续纸版式回归（推翻 2026-09-25 的错误依据）**：2026-09-25「A4 竖版改造」把**四类开单**（销售/退货/拿货/退拿货）一并改成 A4 竖版（210×297、版心 186mm），而实体纸是 241mm 连续纸 —— 内容只占纸宽约 78%，左右各空约 30mm，进一步放大「看不清」的观感。本版恢复秒账式连续纸模型：`@page` 逻辑页 **215.9×139.7mm**（秒账实测几何），页内左右各 **7.3mm** 版心、正文约 **201.3mm**，明细列宽用 mm 定死（13/76.3/16/24/34/38 = 201.3mm），不溢出右缘。实体纸 241×140mm 仍由 `NantianPR-LQ` 按 **A4 横向、实际大小/100%、居中** 映射（两侧各补约 12.55mm）。
- **两套纸型正式分离，不得混用**：**四类开单 = 241mm 连续纸**；**客户货款汇总 / 账款流水 / 往来交易对账单 = A4 竖版（210×297mm）**。配置键也分离：`order_pdf_page_*`（默认 215.9/139.7）只服务四类开单，`printer_paper_*`（默认 210/297）只服务汇总类。`app/erp/utils/purchase_pdf.py` 原先**硬编码 A4**（`@page { size: 210mm 297mm !important }` 并强制覆盖 `order_pdf_page_*`），与「拿货/退拿货 = 连续纸」冲突，本版删除该 A4 覆盖样式表。
- **跨联版心一致**：页框改放在 **`@page` 外边距**（`margin: 6mm 7.3mm`）而非 `.page` padding —— padding 只作用于首/末片段，长单自动进下一联时后续联会丢版心。实测跨联时每联左右版心恒 **7.30mm**、表头重复（`thead { display: table-header-group }`）。**单联可容 8 行**（第 9 行起进下一联）。
- **修掉一个实测发现的缺陷**：9 行以上时页脚（订货电话 / 备注）会被拆到两联上 → `.bottom` 加 `break-inside: avoid`，页脚整块不再拆散。
- **文档回写（撤销被推翻的依据）**：主需求 §3.2 改双模型表述；`AGENTS.md`、项目技能 `SKILL.md`、`references/print-offset-calibration.md`、`references/README.md` 索引同步；`references/sales-slip-paper-241x140.md` **由「历史方案、不得作为当前依据」改回当前依据**（它才是本次修复的正确几何来源），并补入实测字号层级、单联容量与跨联行为。
- 本轮验收：全量 pytest **818 passed / 16 skipped**（v3.6.0 基线 817，新增 1 条字号契约测试 `test_order_print_font_sizes_are_legible_and_ranked`，用真实 WeasyPrint 排版量文本盒字框高，防止某条规则被后写选择器覆盖）。四类开单真实 PDF 逐页 MediaBox `612×396pt`（215.9×139.7mm）、墨迹 L=7.23 / R=6.97（差 0.26mm）、无右溢；汇总/流水/对账 A4 断言全部保留并通过。**未验**：目标打印机实打（`NantianPR-LQ` 纸面位置仍须实机确认）。
- 产物：`dist\local-v3.7.0\简单ERP\`、`dist\simple-erp-setup-v3.7.0.exe`、`dist\simple-erp-windows-v3.7.0.zip`（三种形态同源，per-user 安装不变）。

## v3.6.0 - Windows 安装包形态（2026-10-10，安装包交付）

- **新增安装包形态（Inno Setup 6）**：此前只有 PyInstaller one-folder 绿色包（解压即用）。本版新增 `dist\simple-erp-setup-v3.6.0.exe`：双击安装 → 开始菜单「简单ERP」与可选桌面快捷方式 → 可在「设置 → 应用」正常卸载。构建入口 `打包Windows安装包.bat`（先复用既有 one-folder 构建，再调 `ISCC` 编译）。
- **per-user 安装，不需要管理员**：`DefaultDirName={localappdata}\Programs\简单ERP` + `PrivilegesRequired=lowest` + 禁用提权覆盖。**这是必须的**——程序把可写 `config.json` 放在 EXE 旁边（`erp/config.py: runtime_root()`），装进受保护的系统目录会让非管理员保存设置失败。选择这一形态就不必改业务代码里的路径逻辑。
- **升级不丢店铺设置**：`config.json` 以 `onlyifdoesntexist uninsneveruninstall` 单独安装——重装/升级不覆盖用户改过的店铺名、打印偏移、主题；卸载也保留该文件。
- **数据零接触**：`[Files]` 与 `[UninstallDelete]` 只覆盖 `{app}`，业务数据仍在「文档\简单ERP数据」；卸载后弹窗说明数据位置，不删数据。安装包内不含任何 `*.db`/演示库/日志。
- **前置检查只提示不阻断**：安装前检测 `简单ERP.exe` 是否在运行（在运行则询问是否关闭后再装）；缺 WebView2 运行时只提示并给官方下载地址，不静默联网下载。
- **版本单一真源**：安装脚本用 ISPP 从仓库根 `VERSION` 读版本，`AppVersion=v3.6.0`、PE `FileVersion=3.6.0.0`；脚本里不写死版本号（由 `tests/test_installer_packaging.py` 守护）。
- **简体中文安装界面**：Inno Setup 发行包不带中文语言文件，`packaging/installer/ChineseSimplified.isl` 随仓库提供（官方 `istrans` 版本）。
- **交付产物固定为三种形态**：`打包Windows安装包.bat` 一次出齐 ① 绿色版目录 `dist\local-vX.Y.Z\简单ERP\`、② 安装包 `dist\simple-erp-setup-vX.Y.Z.exe`、③ 交付 ZIP `dist\simple-erp-windows-vX.Y.Z.zip`；ZIP 生成抽成 `packaging/make_release_zip.py`（写盘前拒绝 `.db`/`.log`/`demo_data` 等业务条目）。三者是同一份程序，只差投递方式。只保留当前版本产物——此前 `dist/` 堆了 8 个同名 `简单ERP.exe`，极易误把绿色版当安装包双击。
- 本轮验收：全量 pytest **809 passed / 16 skipped**（v3.5.0 基线 799 + 新增 10 条安装包契约测试）；前端 Node **57 passed**。产物：`dist\local-v3.6.0\简单ERP\`（662 文件，EXE 12,236,517 B，PE `3.6.0.0`/`v3.6.0`）、`dist\simple-erp-setup-v3.6.0.exe`（40,085,898 B）、`dist\simple-erp-windows-v3.6.0.zip`（662 条目，53,548,746 B，SHA256 `2c04bf58…42ea`）。**真实安装/升级/卸载/运行四段实测通过**（隔离目录 + 隔离数据根）：安装文件清单与源目录逐项一致、升级保留改过的 `config.json`、已安装 EXE `/health` 为 `v3.6.0` 且 7 页 200、卸载只剩保留的 `config.json` 且注册表项清空；正式数据根三个文件 SHA256 全程未变。**另经用户真实人工安装验收**：向导中改到自定义路径安装，664 文件与源产物逐项一致、开始菜单「简单ERP」/「卸载 简单ERP」齐全、正式安装 `/health` 为 `v3.6.0` 且 10 页 200。**2026-10-12 收口**：非中文 Windows / 无 WebView2 机器 / 中文用户目录路径按用户产品范围判断关闭（实际使用场景为中文 Windows + 已装 WebView2；这是范围决策，不是验证通过）。绿色包继续保留，三种形态并存。

## v3.5.0 - 筛选无刷新、导航归属、动作标签统一与数据分析图型收口（2026-10-10，打包）

- **筛选提交不再闪烁回顶（Batch A）**：单据管理 / 数据分析 / 账款管理 / 往来对账 / 客户管理 / 商品管理六页的筛选与翻页原本是普通 GET 表单 → 整页重载，滚动容器是内层 `.page-body`，浏览器 `scrollRestoration` 管不到它，实测 7 处 `scrollTop` 全部归 0。新增共享 `app/erp/static/page-refresh.mjs`（`scrollAnchor` / `refreshInPlace` / `wireInPlaceForm` / `initPageRefresh`），给表单/翻页加 `data-inplace`、结果区加 `data-refresh-fragment`，即可 fetch → 只换结果区块 → `pushState` → 恢复滚动；失败回落 `location.assign`。数据分析页原有的 `refreshInPlace` 改为从共享模块 re-export，消除两套实现。**块替换会让直接绑定失效**：往来对账的选中/全选/明细改事件委托；单据管理导出链接的实时同步脚本留在 fragment 之外。实测单据管理 400→400、账款管理 500→500 / 450→450，且 `navigation` 计数不增（真无刷新）。
- **侧栏高亮改按路由 owner 判定（Batch B）**：修「账款管理查出流水后点『往来对账』，左侧高亮跳到数据分析」——该页真实路由是 `/analytics/reconciliation`，`request.path.startswith('/analytics')` 必然命中数据分析，而返回按钮写着「返回账款管理」。新增 `app/erp/utils/nav.py` 做 endpoint → owner 映射，跨模块页面用显式来源参数覆盖（`from=accounts` → 账款管理）；同时删掉「工作台」的 `current` 半高亮类（它让单据管理/账款管理页侧栏两项同时是蓝字）。**不要再用 `request.path.startswith(...)` 判高亮**。
- **动作标签统一（Batch C）**：查看单据统一为「查看」（原「回看」）、编辑统一为「编辑」（原「重编辑 / 编辑草稿」）、打印统一为「打印」（原「查看PDF」）；范围含单据列表、单据详情、工作台最近单据、三个开单页的当天历史面板（**服务端渲染 + JS 重建两处**）、拿货/退拿货详情页。回收站「恢复 / 确认删除」与设置页「下载回看」语义不同，不纳入。
- **条件不可用的动作改为「始终渲染 + 置灰 + 原因」（Batch D）**：同一操作列里有的行有「编辑」、有的行没有，读起来就是标签不一致。不可编辑时不再隐藏，改渲染 `<button type="button" disabled title="原因" aria-disabled="true">编辑</button>`；原因文案集中在 `_document_edit_block_reason()`，**紧挨着**判定函数 `_document_editable()` 放，两者不可能说反。`base.html` 显式写 `:disabled` 样式（灰底 + `cursor:not-allowed` + `opacity:1`）——Bootstrap 默认淡蓝禁用态看着还像能点。API 载荷补 `edit_block_reason`，当天历史面板的 JS 重建分支同步。
- **数据分析图型收口（Batch E/F）**：成本覆盖 / 毛利构成新增「堆叠条 ↔ 环形」切换（`cost_view`，默认仍是堆叠条）——环形段间留缝 + 端头圆角、12 时起顺时针扫掠、悬停段外扩并在圆心显示该段数值；成本缺失来源由横向条形改竖向柱；产品排行维持竖向柱。条形统计图新增「竖向生长」动画：切页签 / 换榜 / 换每组显示 / 显示按钮 / 成本视图切换都播一次，柱子从基线**匀速**长到终高（速度常量 240 px/s，高柱用时更长，实测各柱「高度÷时长」恒定），整页加载与换榜就地刷新后的重放由 JS 加 `.is-growing` 控制（不用纯 CSS 动画），`prefers-reduced-motion` 直接到位。
- 本轮验收：全量 pytest **799 passed / 16 skipped**、前端 Node **57 passed**；新增 23 pytest + 6 Node 契约测试（侧栏归属、动作词表、page-refresh）。隔离数据根 + 真实浏览器 CDP 实测侧栏 17 条路由计算色每页恰好一项高亮、六页滚动保位、环形 12 点起点与悬停外扩、柱状生长速度恒定。**未做**：桌面 EXE 原生窗口/OS 鼠标矩阵、实体打印、浅/深/跟随系统三主题矩阵。本版 fresh 重打 Windows one-folder EXE 与版本化 ZIP（包内只含通用默认配置，不携带业务库、演示库或测试数据）。

## v3.4.0 - 桌面版启动提速与品牌启动画面（2026-10-10，推送与 GitHub Release）

- **惰性导入重依赖（Batch A）**：`weasyprint`/`openpyxl`/`pypinyin`/`PIL` 改为**首次使用时才导入**，涉及 `app/erp/utils/pdf.py`、`purchase_pdf.py`、`exporting.py`、`importing.py`、`pinyin.py`、`routes/products.py` 六个文件。`import erp` + `create_app` 从 **1.85–3.28s 降到 0.445–0.552s**，服务端口就绪 ~2.53s → **~1.13s**。**seam 保留**：`pdf.py`/`products.py` 用模块级 `None` 声明保住既有 monkeypatch 注入点；`purchase_pdf.py` 因测试会直接"读"`purchase_pdf.HTML`，改用 PEP 562 `__getattr__`（原计划的 `None` 写法单独跑会 3 failed）。
- **品牌启动画面先行（Batch C）**：`desktop_app.py` 双击后先开内联 splash 窗口（与 `base.html` 同款 `--erp-*` token 和 `.brand-mark` SVG path，`prefers-color-scheme` 跟随系统浅色/深色），WebView2 冷启动与 Flask 启动并行；服务就绪后切真实首页，**切换失败落错误页**，绝不永久停在 splash。启动顺序必须是「**先同步建 app（数据库失败立即 raise、此时还没开窗口）再开窗口**」——"先开窗口"的实验稿会挂死 `test_main_reports_real_database_failure_before_opening_webview`。
- **splash 抢跑修复（本轮真实根因）**：Batch A 后服务只需 ~0.95s，比窗口出现（~2.0s）还快，而切页线程只等「服务就绪」就切页，导致 **splash 一次都没画出来**、用户全程看到空白窗。改为：切页前等 `events.loaded`（splash **真正绘制**）、splash **至少停留 0.9s**、窗口背景色设为 `#f3f5f8`（消除窗口出现到绘制之间的刺眼纯白）。修复后抓屏确认 splash 出现，冻结版窗口可见 **~1.99s → ~1.31s**（n=5）。
- **已知天花板（如实记录）**：窗口出现那一刻被 **WebView2 冷启动锁在 ~1.5s**，再优化 Python 也压不下去；splash 只是把等待可视化，不缩短总时长。`ui_theme` 显式设为 `dark` 而系统为浅色时，splash 与主界面配色可能不一致（未为此在 splash 前读 `config.json`）。首次 PDF 打印因惰性导入延迟 **+1.61–1.73s**（门店"开机一次、连续开单"净赚）。
- **排除项**（调研已证伪，防止重新提出）：`private_mode=False` 提速（实测无差异）、UPX（只减体积、解压拖慢冷启动）、原生 `--splash`（onefolder 收益有限）、为提速改打印版式或业务规则。
- 本轮验收：全量 pytest **789 passed / 0 skipped**、前端 Node **47 passed**；冻结包功能冒烟 **5/5**（首次 PDF / Excel 导出 / Excel 模板 / 图片上传 / 拼音搜索），隔离页面 200。**真实桌面观感人工验收待用户实机确认**。本版 fresh 重打 Windows one-folder EXE 与版本化 ZIP（包内只含通用默认配置，不携带业务库、演示库或测试数据），已推送 `main`（提交 `bfc8e77`）并发布 GitHub Release `v3.4.0`（ZIP 下载回本地与构建产物字节级一致）；目标打印机实打、深色主题矩阵、独立新电脑与完全断网环境仍未验。

## v3.3.0 - 旧数据迁移承接、备份恢复与零建档起步（2026-10-09，推送与 GitHub Release）

- **备份与恢复（Batch A）**：备份范围补齐 `legacy_archive/`；**恢复能力从零实现** `restore_backup`——先自动备份当前库 → 清 `-wal`/`-shm` → 原子替换 → 复核，失败不留下半套库；备份支持另存到程序目录之外；本库无任何备份时在资料列表页给出告警。
- **客户/商品导入扩列与两段式预检（Batch B）**：客户导入新增电话/地址/**期初欠款**/备注；商品导入新增**单位**/**期初数量**/**期初成本**。导入改为**两段式预检**——先出预览页（含同名策略与**导入总额自检**），用户确认前**零写入**；期初库存联动走独立事务。
- **期初成本来源（Batch C）**：启用期初库存可标成本来源为「已知 / 估算 / 零成本」，支持按**总金额 ÷ 数量**反推单位成本；零期初需显式确认。
- **区间两端余额（Batch D）**：账款流水 PDF 在原有区间筛选上补两端余额与动态标题（A4 竖版实测）。
- **档案来源标记与零建档起步（Batch G）**：商品/客户新增 `origin` 来源标记（`manual`/`import`/`order`，旧数据记为「未知」）；「待补全」口径只看**型号为空**。开单页商品改为**手输 + 联想混合控件**并打通 `spec` 通道（拿货两页仅提示）；开单时手输新名称会**自动建档并写审计留痕**。**G-9**：开单命中回收站里的同名档案时复用并**恢复其可见性**，同时留痕说明，不静默复活。
- **店铺信息引导与顶栏实时钟（Batch E）**：侧栏顶栏新增**每秒刷新的实时钟**；打印用店铺信息（订货电话/地址）缺失时，工作台给出引导卡、设置页给出对应告警与提示开关，填完自动消失。
- **基础资料列表页顶部重排**：商品管理/客户管理顶部由多条整宽玻璃条收成三段式——① 查找 ② 档案筛选（含「待补全」chip）③ 新增/批量导入；后两块改用原生 `<details>` 默认**收起**，日常态一眼看完，表格多露约 95–190px。备份提示由两条常驻合并为**一条按需告警**。
- **商品品牌（Batch E-2）**：商品可选填品牌，不强制。
- **旧账留底存档（Batch F）**：旧欠款明细只落文件存档，不进业务库。
- **单号上限（Batch E-1b）**：MD 取号补 9999 上限处理。
- **品牌区文案清理**：移除侧栏品牌区固定英文副标题 `BUSINESS MANAGEMENT`，只保留店铺名。
- 本轮验收：全量 pytest **773 passed / 16 skipped**（16 skip 为 PDF 文本提取器与 Node 前置未装，与本轮无关）、前端 Node **47 passed**。源码与隔离实例真实页面验收已完成，**未做** E-4（迁移向导，计划内「可裁/后置」项）。正式业务库未迁移投用，目标打印机实打、深色主题矩阵与桌面 EXE 原生交互仍未验。本版 fresh 重打 Windows one-folder EXE 与版本化 ZIP，包内只含通用默认配置，不携带业务库、演示库或测试数据。

## v3.2.0 - 产品审查修复与筛选卡三段式重排（2026-10-08，推送与 GitHub Release）

- **产品审查修复第二轮（Batch A–E + G）**：单据/商品/客户 404 与编辑死路统一中文错误页（全局 404/500/Exception handler），金额解析与收款 ≤0 输入校验收口，导出/PDF 裸文本全部改 `error_response`；列表/详情/当天历史统一 `editable` 口径并给四个开单页补 `beforeunload`；新建页拆「保存」+「保存并打印」（`save_print` 自动标记已打印且 `print_count+1`）并删除死路由 `confirm_print`；账款页新增「往来对账」入口、对账页返回按来源切换；E1–E14 一致性小项（月份输入、状态多选翻页保参、拼音首字母搜索、作废水印、状态点接 `/health`、Bootstrap 本地化等）；**Batch G** 表格改为单层容器（容器内表格去玻璃，玻璃只留最外层卡片，修复暗色下多层玻璃叠加）。
- **筛选卡三段式重排（单据管理 / 数据分析）**：两页顶部筛选卡由 4 等分平铺网格改为三段——① 查询三要素（时间=模式按钮 + 对应日期输入合成**同一组合控件** ｜ 客户/商品/型号）；② 条件多选（订单类型 / 单据状态，虚线分隔）；③ 「已选条件」chip 行（可单独 × 移除）+ 动作条（主操作在左、导出/重置贴右）。**不勾选=全部**，因此移除两个多选组的冗余「全选」按钮，只保留「清空」。chip 规则集中在 `app/erp/utils/filter_chips.py`：时间范围不生成 chip（常驻控件）、四类全勾/无选择都不生成、商品 chip 移除时连同型号一起清、移除链接保留其余条件并去掉 `page`。不改任何筛选口径、导出端点或打印版式。
- 本轮验收：全量 pytest **686 passed**（提取器可用环境，基线同批 670 passed/16 skipped）、前端 Node **44 passed**；隔离数据根 + 隔离 Edge 实测两页筛选卡三档宽度横向溢出 0、动作条不折行、chip 真实点击跳转去条件 URL。正式业务库未迁移投用，目标打印机实打、深色主题矩阵与桌面 EXE 原生交互仍未验。本版含首次合并交付的审查修复提交与筛选卡重排，fresh 重打 Windows one-folder EXE 与版本化 ZIP，包内只含通用默认配置，不携带业务库、演示库或测试数据。

## v3.1.0 - 界面一致性、回收站永久删除与删除自由度（2026-10-07，首次推送与 GitHub Release）

- **侧栏宽度全局统一**：移除 4 个开单页单独收窄到 200px 的覆盖（历史为给右侧 PDF 预览腾宽度），所有页面统一 224px，点开单入口与点其他入口不再出现宽度跳变。
- **品牌区图标**：侧栏 `brand-mark` 由文字「简」改为 Keyline Icons `file-spreadsheet` 内联 SVG（MIT 许可，24×24 stroke 风格，`currentColor`，无外部依赖）；桌面 EXE 使用同一图标。
- **回收站改「左列单据 + 右列资料」**：四类单据合并为一张列表并带类型筛选（销售/退货/拿货/退拿货，含全选/清空与可见计数），右列为商品、客户。恢复对所有类型可用；永久清理仍按类型真实判定（拿货正式/已作废保留库存追溯、退拿货仅无生效历史且无引用的草稿可清理），不可清理的行显示禁用按钮。新增统一批量端点 `POST /recycle/bulk`（键为 `类型:id`，退拿货版本号按 `version_<id>` 提交）。
- **单据管理与数据分析筛选卡统一**：抽出共享 `.filter-card`（4 列满行网格 + 独立订单类型行 + 底部操作条），两页复用；时间模式顺序统一为按年/按月到月/按日到日；订单类型「标签 + 四类复选框 + 全选 + 清空」同一行左对齐，不再把批量按钮推到卡片最右。
- **产品排行柱高修正**：去掉等高槽框，柱高按同榜最大值等比（最高 190px），数值标注紧贴各自柱顶；修复成本缺失条形规则经通用选择器把背景/边框/圆角级联泄漏到竖向柱上的缺陷。
- **流水热力图点日期不再整页刷新**：点击日期改为 fetch 局部替换日历选区与右侧当天明细，保留滚动位置并同步 URL 的 `day=`，不再闪烁跳回顶部。
- **回收站批量按钮修复**：商品/客户卡片的勾选框名为 `product_ids`/`customer_ids`，而按钮走全局 `submitSelected()`（只认 `name="ids"`），两个 form 恒为空，导致「恢复选中 / 确认删除选中」恒弹「请先选中要操作的记录」、只能逐条操作。三张卡片统一改由 `data-recycle-section` + `data-recycle-submit` 驱动，商品/客户走 `/recycle/bulk/<kind>/<restore|purge>`，单据走 `/recycle/bulk`（带 `action` 与退拿货逐行版本号）。
- **正式单据可直接勾选删除（内部自动冲回）**：列表与详情页不再有独立「作废」入口，正式单据可直接勾选删除；底层仍先反向过账（`void_*`）再软删除进回收站，固定原因 `列表删除（自动冲回）`。顺带修掉行内删除四类单据都指向 `/orders/{id}/delete`（销售/退货表）的串表缺陷。
- **删除不再被「末笔/有效退货」拦住（用户决策）**：移除 `void_order` 的「必须是该商品最后一笔库存业务」与「销售单存在有效退货」两道校验，拿货/退拿货的同类「末笔」校验同样移除。依据是用户明确口径：移动均价/成本只是内部预期参考、不据此定售价、不进对外打印。实测删非末笔单据后库存数量、销售金额、账款余额精确，仅均价按移动加权平均重算（可能漂）。「库存不得为负」守恒校验保留。
- **删除失败不再显示裸文本 / 不再吞原因（真 bug）**：后端原以纯文本返回中文原因，浏览器把文字当页面显示；行内删除则固定弹「删除失败，请刷新页面后重试」。新增 `app/erp/utils/errors.py:error_response()` 按调用方分流 JSON 或带侧栏的错误页；行内删除就地插入 `.row-action-error` 提示条显示真实原因。
- **客户「确认删除」静默无效果（真 bug）**：`_purge_one` 客户分支带 guard 的 `DELETE` 在 guard 不满足时影响 0 行且不报错，照样跳转并写审计。改为检查 `rowcount` 并报出「该客户有历史单据或账务记录，只能保留，不能永久删除」；批量 purge 改为逐条处理并报出失败明细。
- **工作台字号上调**：最近业务表 9–11px → 11–13px，指标卡说明/期间徽标/统计日期/快速操作各 +1px；三档宽度横向溢出 0。
- **回看页/详情页移除作废 UI**：删除单据回看、拿货详情、退拿货详情的「作废原因 + 作废」块；`/orders/{id}/void`、`/purchases/return/{id}/void` 路由保留供内部使用；拿货/退拿货详情「移入回收站」对正式单据也直接可用。
- **圆角按钮文字垂直居中**：`.btn` 增加 `display:inline-flex;align-items:center;justify-content:center`。原 Bootstrap `inline-block` + `min-height` 在 flex/grid 父容器里被 blockify 成 `block`，`min-height` 富余全堆到内容下方、文字整体上浮（实测最大 −3px、313 个按钮中 281 个偏移）；修复后 9 条路由共 87 个按钮偏移为 0.00px。
- **回收站「清不掉」真 bug**：`physically_deletable_order` 要求 `status=='draft'`，而正式单据删除走「先作废再软删除」、状态恒为 `void`，判据永远不成立 → 回收站只涨不减，七天自动清理（共用同一判据）同样失效。改为「在回收站 + 无活引用」即可清理。演示库副本可永久删除数 **0/70 → 70/70**；七天清理对 void 单据生效。永久删除按 明细 → 该单据自己的库存流水 → 单据本体 顺序清掉，不留孤儿；引用方（退货明细 → 销售明细）由多趟扫描自动满足先后，被仍在回收站的记录引用时给可读 400 而非 500。
- **新增「清空回收站」**：顶部按钮 → `POST /recycle/empty`，能清的清掉、清不掉的列出原因（`error_response(..., severity="warning")` 中性样式，不是红色报错）。
- **回收站操作列按钮被裁（真 bug，此前记为「已知未修」）**：三张表的操作列把 `action-wrap` 直接写在 `<td>` 上，而 `stable-table` 是 `table-layout:fixed` + `td{overflow:hidden}`，该列只分到剩余宽度的均分零头——实测 1600px 宽下商品/客户卡片操作列仅 66.9px、「确认删除」被裁 **76.1px**，几乎点不到。改为项目通用的 `<td class="action-cell">` + 内层 `<div class="action-wrap">`，**并同步给表头 `<th>` 加 `action-cell`**（`table-layout:fixed` 下首行决定列宽，只改 td 无效）。修复后 1100/1280/1440/1600 四档宽度实测三张卡片 `clipPx` 均为 **−7px**（负值＝按钮完全落在单元格内），页面横向溢出 0。
- 本轮验收：隔离数据根 + 隔离 Edge 真实点击（商品/客户恢复与清理、单据批量删除、退拿货移入回收站均抓到真实 POST 端点与载荷；未勾选 alert 且无提交）；隔离库实测正式销售单删除后库存 `(7000,70000000)→(10000,100000000)`、账款 9000→0、`void_reason` 留痕。全量回归 600 passed / 0 failed，前端 41 passed；隔离 Edge 另验证 4 类删除失败提示（行内就地提示、批量错误页、商品 purge、客户 purge 与批量）均无 alert、无裸文本、带真实原因，演示库副本 70/70 全部删除成功、回收站 70→0 且 `foreign_key_check` 无违规、`integrity_check=ok`、孤儿流水 0。正式业务库未迁移投用，目标打印机实打、深色主题矩阵与桌面 EXE 原生交互仍未验。本版为首次推送 `main` 并创建 GitHub Release 的版本：从当前工作区 fresh 重新打包 Windows one-folder EXE 与版本化 ZIP，包内只含通用默认配置，不携带业务库、演示库或测试数据。

## v2.0.0 - 库存成本、双向往来与正式分析（2026-10-03，本地打包）

- 商品名称/型号身份、期初库存、安全库存、照片和只读移动成本；销售/客户退货保存实际成本快照，微元成本与库存数量守恒，不重算历史。
- 拿货显式关联同一往来对象，增加独立退拿货：原单原行、部分退额度、原实际金额累计分摊、当前移动成本出库及完整作废/回收站保护。
- 销售、客户退货、拿货、退拿货四类交易统一对账，带来源的跨页选择快照与打印前完整校验；交易对账不等于欠款或资金结算。
- 单张拿货/退拿货与四类交易对账A4打印，内部成本/利润/库存/货源采用白名单排除；账款流水按业务单据展开商品明细。
- 正式分析中心接入已确认三页签、跨年热力图、完整排行/分页/排序、筛选保留及成本覆盖口径；商品列表/编辑/分析统一启用状态与库存状况两轴。
- 本地版本号与Windows文件属性同步，打包只含通用默认配置，不携带业务库、演示库或私人配置。构建与新EXE验收结果见v2.0.0本地打包计划；不据此声明Git提交、推送或Release。
- 本轮 fresh 64位Windows EXE与版本化ZIP已生成：529项Python/24项前端回归，真实新EXE的21组分析交互、7组HTTP/6份实际A4 PDF、壳内9组路径初启与重启、通用配置空库首次启动及资源清理通过。原生窗口完整视觉/OS鼠标矩阵、保存对话框人工选择/取消、目标打印机和独立新电脑/完全断网环境仍未验。
- **升级前退出旧程序并备份整个当前数据根，保留原config.json。新程序首次连接旧库会执行内置结构升级；旧历史库存/成本不自动补造，正式期初启用需独立确认。** 本轮只做隔离验收，不迁移或投用正式库；目标打印机实打仍未验。

## V1.1.0 - 同页 PDF 打印预览（2026-09-29）

- 新建销售/退货单成功保存后，原页右侧展开真实 A4 PDF 预览，开单区域同步缩窄；关闭后保留已保存表单与手动重新打开入口。
- 在侧栏直接缩放整页显示，PDF 内部排版不重流；保留独立「保存 PDF」「打印」动作。保存失败不展示预览；PDF 加载失败区分于订单保存失败。
- 开单页侧栏适度收窄；浏览器直接访问 PDF、桌面其他 PDF 预览入口和打印模板不变。
- 本地 Windows one-folder / ZIP 打包验收结果见开单页右侧打印预览执行计划。目标打印机实打仍需另行验收；升级前备份业务数据目录，不覆盖现有数据库。
- 2026-09-30：从当前源码重新构建 fresh V1.1.0 one-folder EXE；隔离 100 条演示数据中由哥哥实际验收统一 PDF 预览、A4 内容及来源感知的「返回上一级」路径，确认此前桌面预览问题已消失。未提交、推送或发布；目标打印机实打和桌面原生保存文件独立核对仍未完成。

## V1.0.0 - 本地桌面版与页面文案收敛（2026-09-29）

- 工作台快捷操作收纳为右侧浮动卡，侧栏按主题显示为扁平浅/深色；设置页支持标准、稍粗、加粗三档界面字体。
- 开单页增加当天有效单据历史；单据管理时间模式与筛选操作区布局更稳定。
- 桌面 PDF 预览页增加返回销售开单入口。
- 收敛工作台和设置页冗余解释性文案；保留业务口径、操作指南、数据迁移与安全提示。
- Windows 全量测试 `156 passed`；本地 one-folder EXE `/health` 与主要页面冒烟通过，设置样张 PDF 实际生成成功；目标打印机未实打。
- 本地 Windows one-folder 与 ZIP；升级前备份当前数据目录，退出程序后只替换程序文件，不覆盖现有 `data\\erp.db`。

## v0.10.0 - 账款流水与经营工作台（2026-09-28）

- 账款页改为统一流水，合并销售、退货、收款与账务调整；明确区分全历史当前余额和日期区间净变动，作废记录保留原因并留有审计记录。
- 新增与账款页面共用统计口径的客户账款流水 PDF，使用 A4 竖版。
- 修复退货单重编辑后金额符号错误；订单状态、明细金额范围与作废原因校验更完整，避免溢出和非法提交产生错误账务或孤立客户。
- 客户/商品联想使用安全文本节点渲染；写请求校验 Origin 与 Fetch Metadata，拒绝跨源业务变更。
- 单据管理增加每页 50 条服务端分页、总数与范围提示；客户/商品联想支持键盘操作，状态显示为中文业务文案。
- 工作台新增今日有效销售/退货净额与笔数，以及最近 5 笔有效单据。
- 全量自动化回归：140 项通过。目标打印机实打、部分 Windows PDF 路径及浏览器控制台检查不在本次自动化结论内。
- 升级前备份当前数据目录；退出程序后仅替换程序文件，不要覆盖现有 `data\\erp.db`。

## v0.9.0 - A4 竖版打印

- 销售单、退货单、客户货款汇总 PDF 和设置页打印样张统一使用 A4 竖版（210×297mm）；销售/退货单按竖版正向重排，旧 241×140mm 单据版心不再沿用。
- 扩展 PDF 回归测试，精确检查生成文件的 A4 页面框尺寸，避免将仅比例正确但被缩小的页面误判为 A4。
- 打印建议选择 A4 纵向、实际大小/100%，关闭“适合页面/缩放”；目标打印机的纸面位置仍需实机单独验收。
- 升级前备份当前数据目录；退出程序后仅替换程序文件，不要用安装包内容覆盖现有 `data\\erp.db`。

## v0.8.0 - 永久免登录

- 打开系统不再要求用户名/密码；浏览器与桌面 EXE 直接进入业务页。
- 去掉登录页与顶栏「退出登录」；`/login`、`/logout` 仅重定向到首页。
- `config.json` 默认关闭 `local_access_password_enabled`，不再强制写入密码哈希。
- `/health` 固定返回 `auth: disabled`。
- 相关鉴权回归测试改为「免登录可进」；全量 pytest 通过。

## v0.7.4 - 设置页打印样张 PDF

- 设置页保留原有 HTML 快速预览，并新增“生成预览 PDF”入口。
- 打印样张使用与销售/退货单一致的模板、A4 页面规格和当前打印设置；桌面端可通过 pywebview 原生保存窗口保存。
- 补充销售单、退货单、两类汇总表和设置样张的桌面预览、浏览器 PDF 返回及保存 API 回归测试。
- Windows EXE 已完成人工验收：中文路径保存、取消、重启后保存和浏览器回归均正常。

## v0.7.3 - 桌面端保存 PDF

- 桌面端销售单、退货单和两类货款汇总 PDF 使用带工具栏的会话内预览页。
- 新增“保存 PDF”按钮：调用 Windows 原生保存对话框，将当前已登录会话中的 PDF 原样写入用户选择的位置。
- 取消保存不会影响预览；浏览器端的原有 PDF 打开、打印和下载流程保持不变。
- 补充 #7 A4 打印修复与 #8 PDF 保存的开发短计划。

## v0.7.2 - A4 上方居中打印

- 销售单 PDF 改为标准 **A4 横向（297×210mm）**，不再模仿秒账的特殊小尺寸页面。
- 241×140mm 单据区固定在 A4 页面上方居中，左右各留 28mm，下方留白，避免打印驱动再次居中特殊 PDF 而造成实打偏移。
- 设置页和打印说明同步改为 A4 横向、实际大小 100%、不要缩放。
- 重新打包 Windows 桌面版压缩包，并补充 A4 版面回归测试。

## v0.7.0 - 销售单 241×140 二联纸版面与当时本机访问配置

- 销售清单 PDF 纸张改为 **一联 241×140mm**（二联纸撕开后用的一张；两联未撕约 280mm 高，不再当单页高度）。
- 版心左右各 **20mm** 对称居中；表格列宽合计 201mm，避免小计列被裁切。
- 页脚（订货电话/地址/主营/备注/制单人/收货人）**紧贴表格下方**，不再贴页面底边。
- 设置页增加打印位置校准说明与毫米偏移/缩放；保存时缺字段不覆盖偏移。
- 调整当时本机访问门的默认账号策略（凭据以当时本地配置为准；该登录门已于 v0.8.0 永久移除）。
- 补充 241×140 版面与打印设置相关回归测试。

## v0.6.0 - 毛玻璃界面、桌面打印免二次登录、Excel 导出

- 界面增加克制毛玻璃层次（侧栏分组、顶栏、卡片/面板、表格外壳等），浅色/深色/跟随系统可用；打印 PDF 不受影响。
- 顶栏固定在上方并保留毛玻璃；页面正文在独立滚动区滚动，避免内容从顶栏透出。
- 左侧业务栏改为实色品牌色 `#012121`。
- 桌面端（pywebview）已登录后，打印预览/PDF 尽量在壳内打开，避免外开浏览器导致二次登录。
- 新增 Excel 数据导出（`.xlsx`）：客户、商品、销售单、退货单；与现有货款汇总 PDF 预览区分。
- 单据导出按明细一行；销售/退货导出可跟随客户与日期筛选；未登录不可下载（v0.6.0 时的登录门规则，已被 v0.8.0 永久免登录取代）。
- 补充桌面打印鉴权、Excel 导出相关回归测试。

## v0.5.0 - 设置页、数据目录、登录与打印页脚

- 新增侧栏「系统 → 设置」：公司名称、界面缩放（100%/125%/150%）、主题（浅/深/跟随系统）、打印单文案、数据目录。
- 公司名称只改一处，同步顶栏、销售/退货 PDF、账款汇总 PDF 抬头与系统名称。
- 打印单可配置订货电话、地址、主营、备注、制单人、收货人；提供预览（示例数据不写库）。
- 打印页脚改为固定两列表格布局，制单人与收货人在 PDF（WeasyPrint）中齐平。
- 设置有未保存修改时切换页面，询问：保存并离开 / 不保存离开 / 取消。
- 数据目录：默认与程序分离；路径记忆在 LocalAppData；设置页一键迁移（目标已有库拒绝覆盖）；桌面版支持系统选夹。
- 新增可选单账号本机访问密码门及登录界面；`/health` 无需登录。
- 复用配置字段 `local_access_username` / `local_access_password_enabled` / `local_access_password_hash`。
- 桌面窗口标题随公司名称变化；补充 README 与 Windows 打包/升级说明。
- 补充设置、登录、打印页脚相关回归测试。

## v0.4.0 - 单据管理、客户统计、导入与 MD 单号

- 单据查询更名为单据管理；侧栏与工作台入口同步更新。
- 单据管理支持按年 / 按月到月（可跨年）/ 按日到日筛选，并与列表、采购统计共用时间范围。
- 新增订单类型筛选：全部、只看销售单、只看退货单。
- 客户采购统计：唯一客户自动出图；模糊命中多个客户只列表并提示；空客户显示全店拿货统计。
- 采购热力图按自然月分块展示，月份之间留间隙；宽屏约 6 月一行，窄屏自适应换行。
- 商品排行按累计数量排序，提供前 5 / 前 10 与明细表；宽屏下右侧柱图自适应列宽。
- 单据管理增加“导出汇总表”：新开 PDF 预览；唯一客户单段、多客户按客户分段；销售单与退货单都进表，与类型筛选无关。
- 账款管理页面保留不删，旧入口继续可用。
- 汇总表 PDF 加宽单号列并允许格内换行，避免长单号溢出。
- 商品管理、客户管理支持 Excel/CSV 模板下载与批量导入；同名跳过不覆盖；失败时仍显示现有列表与中文错误。
- 订单编号改为 `MD` + 八位日期 + 四位流水，如 `MD202607120001`；销售单与退货单共用当天流水。
- 新建销售/退货：日期默认当天可改，改日期后单号按所选日重算；单号只读。
- 重编辑保持原单号与原日期，仅允许修改业务内容。
- 补充客户统计、导入、汇总导出、MD 单号与筛选相关回归测试。

## v0.3.0 - ERP 界面重构与录入体验优化

- 重构桌面版整体 UI，新增企业 ERP 风格工作台、分组侧边栏和统一的表格、表单、按钮视觉体系。
- 优化销售单与退货单录入页面，增加“一键清空”，并保持默认一行的 Excel 式录入流程。
- 关闭浏览器对客户、商品、数量、单价、单位和备注等开单字段的历史自动填充，同时保留 ERP 自身的客户与商品联想提示。
- 将账款页逐客户重复查询改为一次聚合查询，显著提升客户余额页面加载速度。
- 调整销售清单打印页，将制单人和收货人并入订货电话、订货地址右侧，并优化签字区域对齐和宽度。
- 增加 UI、账款余额、自动填充和打印布局回归测试。

## v0.2.2 - 重构桌面版保存打印流程

- 保存/打印改为先通过 fetch 异步保存订单，再用纯 GET 地址打开 PDF。
- 移除 POST 表单的 `target="_blank"`，避免 PyWebView/WebView2 新窗口丢失请求方法导致 405。
- 保存成功后显示订单号，并提供“打开打印页”“查看订单”“继续开单”按钮。
- 自动打开打印页失败时保留手动打印入口，避免用户重复提交订单。
- 保存期间禁用按钮，防止连续点击产生重复订单。
- 销售单与退货单共用同一套保存打印逻辑。

## v0.2.1 - 修复保存打印跳转方式

- 修复桌面版点击“保存/打印订单”后浏览器显示 `Method Not Allowed` 的问题。
- 保存打印重定向改为 303 See Other，确保浏览器用 GET 打开订单 PDF。

## v0.2.0 - 退货单与账款汇总打印优化

- 新增退货单开单入口，与销售单共用录入体验。
- 侧边栏开单下增加销售单、退货单快捷入口。
- 单据页和账款页对退货单行进行红色标注。
- 退货单支持保存后直接打开打印 PDF。
- 账款页新增清除筛选条件。
- 商品管理、客户管理新增相似字符查询和清除信息。
- 客户货款汇总表支持退货单红色标注。
- 客户货款汇总表底部展示“销售总金额 - 退货总金额 = 总金额”。
- 客户货款汇总表对同一单号的总金额进行合并显示，避免重复展示。
- 更新 README，补充当前功能、使用方式和仓库架构说明。

## v0.1.0 - 初始可用版

- 完成开单、商品、客户、账款、回收站基础流程。
- 支持客户价、常规价、保存/打印订单、客户货款汇总表。
- 完成 Windows 桌面版 EXE 打包，门店电脑可双击运行。
