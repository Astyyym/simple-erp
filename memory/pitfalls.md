# 踩过的坑

## 备份与恢复的范围

`utils/backup.py:create_backup` 覆盖 `erp.db` + `config.json` + `product_images/` + `legacy_archive/`。
恢复已实现 `restore_backup`：**默认不动 `config.json`**，恢复前自动建 `pre_restore` 备份。

## 手机只读端（第二监听 5001）的两个硬坑

**Windows 上「端口被占」不能用 bind 异常判定。** `SO_REUSEADDR` 在 Windows 允许**重复绑定**
已在监听的端口（与 POSIX 语义不同），waitress `create_server` 会「绑定成功」然后行为诡异。
判据必须用 connect 探测（`socket.connect_ex(("127.0.0.1", port)) == 0`），见
`app/erp/mobile_server.py:start_mobile_listener`。2026-10-17 实测：另一个 socket 已 listen 时
`start_mobile_listener` 仍返回成功。

**headless Chrome 的窗口宽度被夹到最小 500px。** `--window-size=390,844 --screenshot` 出来的
PNG 确实是 390×844，但**布局视口是 500 宽**，于是截图右侧被裁、会把「窄档正常」看成「内容溢出」。
`--headless=old` 同样被夹。验证 390 手机布局的正确做法：外层页面放一个 `390×844` 的 `<iframe>`，
用 `--window-size=500,1000` 截图后裁到 `(0,0,390,844)`。自证方法：页面内探针把
`innerWidth` 写进 `document.title`，`--dump-dom` 里 grep（实测 `vw=500x749`）。

## 桌面版关掉之前别跑全量测试

`dist\local-vX.Y.Z\简单ERP\简单ERP.exe` 在跑时会占 5000。此时**不带** `ERP_PORT` 跑全量，
`tests/test_desktop_startup.py::test_main_reports_real_database_failure_before_opening_webview`
会走「已有实例」分支，真的去 `webview.start()` 开窗并**一直挂住**（表现为该测试卡死、整轮超时），
不是回归。判据：`Get-Process -Id <占用 PID>` 看是不是用户的桌面版。

## 取「局域网 IP」不能只看默认路由（本机有 TUN 代理）

本机装了 Meta（Clash 系）TUN 代理，网卡地址是 `198.18.0.1`（RFC 2544 基准测试段）。
`udp connect("8.8.8.8", 80)` 再 `getsockname()` 这种常见取地址法，在这台机器上返回的是
**`198.18.0.1`**，不是真实的 `192.168.10.5`（WLAN）。用它生成二维码/配对地址，手机会永远连不上，
而电脑端一切「看起来正常」。

判据与做法（`app/erp/mobile_server.py`）：

- 候选取两路：默认路由（连几个公网 IP）+ `socket.gethostbyname_ex(hostname)[2]`（本机返回
  `192.168.10.5` 与 `198.18.0.1`）。
- 过滤掉 `198.18.0.0/15`、`169.254.0.0/16`、`127.0.0.0/8`，只留 RFC1918；
  排序 192.168 > 10. > 172.16–31。
- 一个都没剩时不生成二维码，并给「先检查电脑 Wi-Fi」的提示，不要显示 127 地址的二维码。

## 客户与供应商共用一张表

`customers` 表同时存客户和供应商，`name` 有 UNIQUE 约束，**软删除的记录也占名**。
→ 判断「能否新建/导入」时不能只看有效记录，必须按含软删除的唯一性来判断。
（业务规则见 `docs/需求/拿货与对账业务契约.md`；导入细节见 references/`customer-analytics-and-master-data-import.md`。）

## 数据根排查的误判

别把 `-wal` / `-shm` 文件或主库 mtime 当成「有人在写」——**只读连接也会创建它们**。
判据用 WAL 大小 + 结构指纹。详见 references/`data-root-probe-pitfalls.md`。

## 打包产物的内容门

安装包与 ZIP **不得含演示数据**；绿色版才带 200 条演示数据。构建顺序固定为：
干净产物 → 安装包 → ZIP → 绿色版 → 写演示数据。
详见 references/`portable-demo-package.md` 与 `AGENTS.md` 的「交付产物」一节。

## 只读探测也会弄脏交付产物

`sqlite3.connect("file:<db>?mode=ro")` **仍会在 WAL 库上留下 `-wal`/`-shm`**（本机实测：探完
绿色版 `demo_data\data\erp.db` 多出 0B `-wal` + 32KB `-shm`）。核对交付产物里的库必须用
`file:<db>?mode=ro&immutable=1`；已经弄脏就按规矩收尾——读写连接 + `PRAGMA wal_checkpoint(TRUNCATE)`
再删残留文件，最后确认 `demo_data\data\` 只剩 `erp.db` 且 `integrity_check` 仍 ok。

## 删表删列时，残留扫描必须用「代码级」口径

`grep` 原始词表会把**解释「为什么删」的注释与 docstring** 一起算成命中。2026-10-16 删除库存系统时实测：
`app/erp` 内原始 grep 约 60 处命中，用 `tokenize` 剔除 `COMMENT` / `STRING` 之后是 **0 处**——
活代码一处都没有，命中的全是迁移说明。

→ 判定「某概念是否真的在代码里消失」必须走代码级扫描；把原始 grep 的命中数当结论，会被自己的注释淹没。
（模板/JS 不能 tokenize，那类只按逐文件残留 grep + 页面冒烟 + `node --check` 判定。）

## 别把「表的列数」写死在断言里

`tests/frontend/order_slip_notice.test.mjs` 原来断言换页提示行 `cell.colSpan === 8`。
删掉成本列后模板改成 7 列，**模板改了、断言没改**，于是 8 个用例一起红；
而 `orders/new.html` 的上游改动**恰好漏改了这一处 `colSpan`**，两边互相掩盖。

→ 断言列数要从**被断言对象自己**推导（该测试改成读模板 `<thead class="table-light">` 里的 `<th>` 个数），
不要写常量。这类「模板与测试各记一份数字」的结构，在删列/加列时必然对不上。

## 子代理的「已完成」必须自己复核

2026-10-16 的 UI 改动里，一个子代理自报「换页提示行 `colSpan` 8→7 已修」，实际**没落盘**；
另有子代理把退拿货页的提示位 id 改名（`costHint`→`sourceHint`）——那同时是 3 处 JS 的消息通道，
删或改名稍有不慎就抛 `TypeError`。

→ 子代理的产物一律按「文件级证据」复核：残留 grep、页面 200、`node --check`、关键 id 引用一致性。
自报的通过数不能当证据。

## 发布提交要用显式 pathspec

工作区常驻「前序会话/用户自己的未提交批次」（`.gitignore`、几个 `.bat`、未跟踪的 `memory/`、
已 staged 的短计划删除）。`git commit -a` 会把它们一起卷进发布提交；发布提交一律
`git commit -- <明确路径…>`，并在提交后核对 `git status` 里那批仍然原样留着。

**但已 staged 的删除不吃 pathspec（本机实测）**：`git commit -- 开发短计划` 只带走了该目录下
**被修改**的那个文件，68 个 staged 删除一个都没进提交，而且提交后 index 里那批删除也消失了
（`git diff --cached` 变空、HEAD 里文件还在）。删除类改动一律单独走
`git rm -r --cached <目录>` + **不带 pathspec** 的 `git commit`（此时 index 里只有这一批），
提交后用 `git -c core.quotepath=false ls-tree -r HEAD --name-only | grep -c "<目录>/"` 复核为 0。

## .gitignore 不会停止跟踪

`.gitignore` 只对**未跟踪**文件生效。已入库的文件加了 ignore 规则照样留在仓库里、照样会被发布；
必须 `git rm -r --cached <路径>` 显式移出索引（本地文件保留）再提交。
核对：`git ls-files <路径>` 与 `git ls-tree -r HEAD` 都应为空。

## 勾选框单元格里凭空出现的省略号

`.stable-table th,td` 带 `overflow:hidden` + `text-overflow:ellipsis`。当一个单元格里只有
一个 16px 的**裸 inline-block 复选框**、且该格可用内容宽只有 16px（44px 减左右 padding 各 14px）时，
Chrome 把这个控件算成 **55–56px 的溢出内容**，于是在一个**内容完全为空的格子里画出一个省略号**。
2026-10-16 实测：单据管理 1 个表头 + 21 个数据行 = 22 处，每行第一个格都多出三个白点。

- **确诊**：注入 `text-overflow:clip` 做对照——`ellipsis` 下白点出现、`clip` 下消失。不要靠肉眼/模型描述下结论。
- **修法与判据**：`display:block;margin:0 auto`（实测溢出 `scrollWidth 56` → `44` = `clientWidth`，且复选框
  几何**逐像素零位移**）。不要用 `text-overflow:clip`（那是让溢出消失、不是消除溢出），也别依赖 `:has()`
  （老 WebView2 不一定支持）。
- **判据查询**：遍历 `th,td`，`cell.querySelector('input[type=checkbox]')` 且 `scrollWidth > clientWidth + 0.5` 即中招。
- 商品管理页原先各自打了半套补丁（写死宽度 + 压 `::before/::after`），统一到 `base.html` 后已删除。

## 段导航/目录脚本必须放在被引用分区之后

页内「吸顶段导航 / 左侧目录」的滚动高亮脚本要 `document.getElementById` 拿各分区算当前位置。
若脚本排在分区**之前**（模板中间），`getElementById` 拿到 `null`，`.filter(Boolean)` 后数组为空
→ 直接 `return`，**静默失效**：不报错、无控制台异常、类名就是不出现（2026-10-16 实测踩过一次）。
→ 这类脚本一律置于所有分区之后（模板末尾）。验证要看 `classList` 是否真被加上，不能只看脚本在不在页面里。

## 段导航高亮不能在初始化时闭包捕获分区节点

就地刷新（`refreshInPlace` → `importNode + replaceWith`）会**整块替换**结果分区，
初始化时捕获的节点随即 `isConnected=false`、`getBoundingClientRect()` **全为 0**；
若逐段比较写成 `el.top - paneTop <= 8`，则**每一段都成立**，循环走完停在最后一段 ——
表现为「高亮永久卡在最后一项」，且监听绑在内层滚动容器上不会重绑，刷新一次就再也不恢复。
2026-10-16 实测：分析页首次加载正常，点一次「查询」后恒亮「具体产品」。
→ `sync()` 内每次重新 `getElementById`，并监听 `erp:refreshed` 在刷新后补算一次。

## headless Chrome 的 setDeviceMetricsOverride 不改 window.innerWidth

本机实测（Chrome 141 / Windows）：CDP `Emulation.setDeviceMetricsOverride` 之后
`window.innerWidth` 仍报 **1578**，`matchMedia('(max-width:1100px)')` 恒为 `false`，
于是所有窄档媒体查询都不命中 → 会把「窄档 CSS 没生效」当成真缺陷去改代码（假失败）。
→ 窄档验证必须用 `--window-size=1100,900` 开**独立浏览器实例**，并先打印 `window.innerWidth` 自证。

## 窄档 UI 回归必须「同档位」对比改前基线

判断「窄档是不是变差了」不能拿宽档改前值去比窄档改后值。2026-10-16 实测：单据管理筛选卡
1440 改前 357px、1100 改前 **439px**；我误用 357 对 1100 的改后 366，得出「窄档反弹」的**错误结论**，
并按错误结论把条件栏改成两列，结果 1100 档实际恶化到 390px。
→ 改前基线用 `git stash push -- <paths>` 取已提交状态实测（测完 `git stash pop`），
并注意 stash 的 `LF will be replaced by CRLF` 只是 autocrlf 提示，恢复后要按字节核对行尾仍是 CRLF 无混合。

## 改结构后要连旧容器的布局属性一起删，否则容器属性会「接管」新子元素

2026-10-17 实测：分析页「金额汇总」段的结构早已从「三张卡直接铺在容器里」改成
「`.section-heading` 标题 + `.analytics-amount-grid` 卡片网格」两个子元素，
但容器 `.analytics-summary` 上的 `display:grid;grid-template-columns:repeat(3,...)` **没删**。
于是标题与卡片网格各自变成一个 grid item、各占一列：标题只占 1/3 宽，
六张卡被压进 391px（每张 **90.25px**），`¥24012.03` 实需 110px → **溢出 52px**、退货金额折两行。

**为什么当时的验收没发现**：只核了「列数=4」与「页面级横向溢出=0」——后者恒成立，
因为卡片带 `min-width:0`，会**把溢出吃进自己内部**，`document.scrollWidth` 一点不涨。

→ 判据要落在**卡片自身**：`el.scrollWidth > el.clientWidth`（数值元素与卡片各测一次），
不能只看页面级 `scrollWidth`。
→ 改版时把「结构」与「容器布局属性」当成一件事：结构变了就 grep 旧容器类名的残余规则，
本轮同时清掉了已无模板使用的 `.analytics-flow-grid` 死规则。

## sticky 元素的包含块是父级盒，中间加一层容器就会让它失效

设置页左栏原本是 `nav.settings-toc{position:sticky;top:0}` 直接挂在 grid item 上（包含块 = 高的内容区）。
2026-10-17 为了让「保存设置」成为独立卡片，外面包了一层 `.settings-side`（flex column，高仅两卡之和 280px），
sticky 的包含块随之变成这一层 → 滚动超过 280px 后整列滚出视口
（实测：滚 600px 时 toc 的 `top` 由 192 掉到 **−320**，「左栏常驻」静默失效，页面不报错）。

→ 加包裹层时，sticky 要跟着**上移到包裹层**（`.settings-side{position:sticky;top:0}`），
窄档改横排时再关掉（`position:static`）。
→ 验证方式：滚动到两个不同位置读 `getBoundingClientRect().top`，**两次相同才算粘住**；
注意 sticky 粘的是容器 padding 内边缘（本页 `.page-body` padding-top 20px），
别拿 `pane.getBoundingClientRect().top` 当基准去比，那会得出假的「没生效」。

## refreshInPlace 只替换「当前文档里已有」的同 id 区块

`page-refresh.mjs:refreshInPlace` 遍历**新页面**的 `[data-refresh-fragment]`，再 `doc.getElementById(id)`——
**当前文档里没有这个 id 时既不插入也不报错**（`replaced` 计数没人检查）。
文件头注释写着「新页面多出的区块会被插入」，实现里没有这条。

后果：从「无结果区块」切到「有结果区块」时结果永远不出现，而 URL 已被 `pushState` 更新
→ 用户看到的就是「点了查询没反应」。2026-10-17 实测复现：**未改动的旧代码**账款管理页
（`/accounts/` 未选中客户 → 下拉选客户 → 点「查询流水」）提交后 `#accountsResults` 仍不存在、流水 0 行，
地址栏却已变成 `?customer_id=28`；往来对账页「未选中 → 选对象 → 查询」同样中招。

→ 同一块 UI 的**空态与结果态必须共用同一个 fragment id**（本轮：账款页空态 section 也用
`id="accountsResults"`；对账页空态 div 用 `id="reconPanel"`），id 一一对应后两个方向都能就地替换。
→ 新增「未选中 / 空态」分支时，别给它新 id，也别漏掉 `data-refresh-fragment`。
→ 同源的第二处：**「已选条件」chip 行**原先只在有 chip 时才渲染，于是「首次筛选 → chip 从无到有」
同样不出现（2026-10-17 分析页实测：筛了客户却看不到 chip，也就没有一键移除入口）。
修法同样是让容器常驻：`<div class="filter-chips{% if not filter_chips %} is-empty{% endif %}" data-refresh-fragment id="analyticsChips">`
+ `.erp-analytics .filter-chips.is-empty{display:none}`。
→ 对照实验做法：拿**未改动的旧代码实例**跑同一条路径，能干净地区分「既存缺陷」与「本次引入的回归」。

## flex 容器收不窄：要让一组控件换行并变窄，用 grid 不用 flex

2026-10-17 实测：分析页筛选区要把「时间范围 + 模式按钮 + 起止日期」这块从 596px 收到 ~290px
（腾出宽度给右侧并排的客户 / 商品），先试 `.filter-inline{flex-wrap:wrap}` + `.dates{flex:1 1 100%}`——
dates 确实换行了，但**容器宽度一点没变**（仍是 596px）。
原因：flex 容器的 **max-content 宽度按「所有子项不换行之和」算**，换行不参与 intrinsic size 计算。
改用 grid（`grid-template-columns:auto auto` + `.dates{grid-column:1 / -1}`）后，容器宽度 = 各列 max-content
之和（标签列 + 按钮列）≈ 296px ✓。

→ 判据：改完要读**容器自身**的 `getBoundingClientRect().width`，不能只看子项有没有换行。
