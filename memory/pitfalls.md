# 踩过的坑

## 列表行描边要跟着**圆角**走，否则深色下会露出一圈直角方框（2026-10-23）

`Modifier.listRowBorder` 原来左右两边是**从行顶画到行底**的两条直线。首/末行用的是
`listRowShape`（圆角），直角描边就从圆角外侧探出来。浅色模式下 `border` 是 alpha 0.07、
几乎看不见；深色模式换成 alpha 0.09 的白色后，它变成一圈明显的**直角方框**套在圆角卡外面
（用户直接圈图报「深色模式下能看到圆角矩形后面的直角矩形边框」）。

**判据**：深色截图放大 8 倍看首行左上角，圆角外侧不应有 1px 直线。
**修法**：竖线只在没有圆角的区段画（`top + topRadius` 到 `bottom - bottomRadius`），
圆角用 `drawArc` 补 180°/270°/0°/90° 四段，半径取 `Dimens.radius`，
并在 `inset = stroke/2` 处画，才和 `ErpCard` 的 `.border(1dp)` 严丝合缝。

## 固定高度容器 + 会折行的文本 = 静默裁字（2026-10-23）

单据详情抬头是一个**固定高度**的折叠容器。客户名 `宏图建设集团第三项目部` 折成两行后，
下面那行 `10月23日 MD…` 被顶出容器、整个消失 —— 用户看到的是「单号没了」，
而不是「有个东西被裁了」（Compose 不报错）。

**规矩**：
1. 固定高度容器里放可能折行的文本时，高度要**按实测行数**算，不能按字数猜。
   `TextMeasurer` + `Constraints(maxWidth = …)` 量一次 `lineCount` 最可靠，
   可用宽要把页边距、卡片内距、**同排其他元素的实测宽**（这里是金额）都减掉。
2. 同一行里「名字 + 金额」时，名字用 `weight(1f)` 吃剩余宽度、金额不加权重 ——
   否则金额会被长名字挤成两行或截断。
3. 用 `fill = false` 的 `weight` 会让文本按最小需要宽排版，长名字反而更容易把邻居顶走。

## 排序切换「不回顶，在原位置刷新」= 回顶调早了（2026-10-23）

现象：客户/商品列表滚到第 20 行，点正序/倒序或换排序依据，**画面停在原行号**，
只是内容换了一批，看起来像「排序没生效」。

原因不是「忘了 `scrollToItem(0)`」（上一版就有），而是**顺序错了**：
`LazyColumn` 在数据顺序变化时会**按 item key 重新锚定**视线。先滚到 0，
紧接着新顺序上屏，它会把「原来的第一行」（现在可能排到第 40 行）重新拉回视野。

**规矩：回顶必须等新数据真正上屏之后再做。**

```kotlin
// ViewModel：只置一次性信号，滚完就清
fun refreshCustomers(resetScroll: Boolean = false) {
    viewModelScope.launch {
        loadCustomers()                                   // 先加载
        if (resetScroll) _customersScrollReset.value = true
    }
}

// 列表页
LaunchedEffect(signal) {
    if (!signal) return@LaunchedEffect
    withFrameNanos { }        // 等一帧，让新顺序完成测量与锚定
    listState.scrollToItem(0)
    onConsumed()
}
```

用**一次性信号**而不是 `LaunchedEffect(sort, descending)`：后者在重新进入组合时也会跑，
切 tab 回来就丢位置（2026-10-22 已踩过一次）。单据页换日期/类型/客户用同一机制。

## `alpha = 0` 的 `Modifier.clickable` 照样吃点击（2026-10-23）

折叠/展开两态叠在同一个 `Box` 里、只用 `alpha` 交叉淡入时，**透明的那一层仍然接收点击**。
实测：单据页折叠态那一行（高 42dp、居中）正好盖住展开态的时间按钮，
点时间按钮**毫无反应**，日志无异常、状态也没变，极容易误判成「状态没更新」。

**规矩**：两态并存时按 alpha 阈值决定是否进入组合：

```kotlin
if (expandedAlpha > 0.02f) { /* 展开态 */ }
if (collapsedAlpha > 0.02f) { /* 折叠态 */ }
```

淡入淡出的观感保留，隐形的点击层消失。

## 固定高度容器里放 wrapContent 的卡片会裁掉内容（2026-10-23）

单据详情抬头是「固定高度容器 + 内部卡片」做折叠动画。写 196dp 时卡片下面空出一大块；
改 120dp 时**日期行被裁掉一半**（字号/行高随系统字体缩放一变就更糟）。

**规矩**：折叠容器定高，内部卡片 `fillMaxSize()` 顶满、内容顶对齐。
这样容器收起时卡片跟着变矮（这就是动画本身），展开时也永远不会裁到字。

## 别用 PowerShell `-replace` + `Set-Content` 改含中文的源文件（2026-10-23）

`(Get-Content x) -replace ... | Set-Content x` 会用默认编码重写整个文件，
`native/app/build.gradle` 里的中文注释全变成 `?`（`与 Python 端 VERSION 无关` → `�?Python �?VERSION 无关�?`），
diff 从 1 行变成 7 行、还看不出来哪里坏了。

**规矩**：改源码一律用 `edit` 工具做字面替换；确实要整文件写就用 `[System.IO.File]::WriteAllText`
（UTF-8 无 BOM）并立刻 `git diff` 核对行数与编码。发现被写坏就 `git checkout -- <file>` 还原再重做。

## 下拉刷新「图标不转、转完不消失」= ViewModel 里 launch 套 launch（2026-10-23）

`PullToRefreshBox` 的 `isRefreshing` 由 ViewModel 的 `_refreshing` 驱动。原来的写法：

```kotlin
fun pullRefresh(tab: Tab) {
    viewModelScope.launch {
        _refreshing.value = true
        try { refreshToday() }   // ← 它自己只是 viewModelScope.launch{} 就返回
        finally { _refreshing.value = false }
    }
}
```

`refreshToday()` 立刻返回 → `finally` 在**网络请求发出之前**就把 `refreshing` 置回 false。
指示器的动画状态机被拦腰打断，表现为：不转、也不消失，只有切 tab 重建整棵树才清掉。

**规矩**：任何「用户可见的进行中状态」（下拉刷新、保存、上传）都必须在**能 await 到
真实结束**的那一层置位/复位。把 `refreshXxx()` 拆成「suspend `loadXxx()` + 外层
`launch` 包装」，`pullRefresh` 直接 await `loadXxx()`；再补一个最短可见时长
（`MIN_REFRESH_VISIBLE_MS`），否则局域网 50ms 就回来，指示器一闪而过同样像没反应。

**自证方法**：`adb shell input swipe` 造下拉手势，紧接着 `screencap` 抓 mid 帧 —— 必须能
截到 spinner；再等 1s 抓一帧，spinner 必须已消失。

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

## 手机只读端：防火墙弹窗与配对是「时机」问题

**安装时不弹，开启手机访问那一刻才弹。** 2026-10-17 真机实测：Inno 安装包装到自选目录、
启动程序全程无弹窗；在设置页点「打开手机访问」让 EXE **首次监听 `0.0.0.0:5001`** 时，
弹一次「网络」提示。点「允许」后系统写入两条入站 Allow 规则（TCP/UDP，规则名「简单ERP Windows …」），
手机随即能连。→ 排查「手机连不上」时，第一个问题就该是「开开关时有没有点允许」。

**源码模式永远看不到这个弹窗**：socket 实际由 venv 背后的基础解释器持有，那条路径早就有允许规则。
所以「源码自测没弹窗」不能当成「不会弹」的证据。

**重装/升级不丢配对**：设备记录在数据根 `data/mobile_devices.json`（卸载不删数据），手机侧 cookie
也在浏览器里。重装后扫码直接进、不再要配对码是**预期**，不是门没关上。要断开只能在桌面端撤销设备。

**同一台手机重复配对会产生多条设备记录**（没有设备指纹，每次配对都新增一条）。
设置页看到两台「Android 手机」是正常的，撤销多余的那条即可。

## 安装/卸载/重装：卸载会丢掉「上次安装目录」（2026-10-17 已修）

Inno 把上次安装路径存在 `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{AppId}_is1`
的 `Inno Setup: App Path` 里，**卸载会把整个键删掉**。于是「装到 D 盘 → 卸载 → 重装」
会静默回到 `DefaultDirName`（`%LOCALAPPDATA%\Programs\简单ERP`），并在那里生成一份**全默认的
config.json**：店铺名、打印偏移、主题、手机开关全部看起来「丢了」，其实原件还躺在旧目录
（`onlyifdoesntexist` 只保护同一个 `{app}`）。2026-10-17 实测复现。

**已修（2026-10-17）**：`[Code]` 在 `ssPostInstall` 把 `{app}` 写进
`HKCU\Software\简单ERP\LastAppDir`（卸载不会删这个键），`DefaultDirName={code:GetLastAppDir}`
读它，读不到才回退 `{localappdata}\Programs\简单ERP`。实测：装到 `D:\app\简单ERP-test` → 卸载 →
不带 `/DIR` 重装仍落回该目录且 `config.json` 逐字节不变；删掉注册表键后全新安装仍正确落到默认目录。
`tests/test_installer_packaging.py::test_installer_remembers_previous_install_dir_across_uninstall`
守死这条，**别把 `DefaultDirName` 改回纯常量**。

→ v4.3.0 及更早的安装包没有这个修复：重装时用 `/DIR=<原目录>`，或在向导里选回原目录。

**卸载后 `{app}` 只剩 `config.json` 是正常的**（`uninsneveruninstall` 的预期），不是残留 bug；
卸载日志里的 `Failed to delete directory (145)` 就是「目录非空」——145 = `ERROR_DIR_NOT_EMPTY`。

**别用文件 mtime 判断「升级有没有真的替换程序文件」**：安装器把 mtime 复原成源文件时间戳，
同版本覆盖安装后 `简单ERP.exe` 的 mtime 不变，但日志里有 `Installing the file.` —— 文件是真重写了。

**自己跑冒烟测试留下的防火墙规则要按 Program 删**：Windows 首次监听 `0.0.0.0` 时按 EXE 名生成的规则
（`TCP/UDP Query User{...}`，DisplayName 就是 `简单erp.exe`）会跟着 EXE 路径走；EXE 被删后规则仍留着。
删除需要管理员，且别按 DisplayName 精确匹配（中文/大小写不稳），用
`Get-NetFirewallRule | Get-NetFirewallApplicationFilter` 的 `Program -like '*<路径片段>*'` 过滤。
（2026-10-17 实测：按 DisplayName 精确匹配拿到 0 条，按 Program 才删到那 2 条。）

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

## 给 Inno 传 `/DIR=` 参数时，反斜杠必须在 Python 里写原始字符串

2026-10-17 实测：用 `subprocess.run([setup, "/DIR=D:\app\简单ERP", ...])` 传安装目录，
`\a` 被 Python 当成转义 → 实际参数变成 `D:\x07pp\简单ERP`（BEL 字符）。
Inno 不报错，日志里能看到 `Setup command line: ... /DIR=D:\u0007pp\简单ERP`，
`{app}` 落回 **当前工作目录**（= 仓库根），于是 679 个文件、`unins000.exe`、
`HKCU\...\Uninstall\{AppId}_is1` 和 `LastAppDir` 全指向了源码仓库根目录，
而安装器退出码仍是 **0**。

→ 传路径参数一律用原始字符串（`r"/DIR=D:\app\简单ERP"`）或 `pathlib`/`os.path` 拼，
   不要写普通字符串字面量；
→ 静默安装**必须带 `/LOG=`** 并在装完后核对日志里的 `Setup command line` 与
   `Directory for uninstall files`，不能只看退出码；
→ 装完立即核对三处：PE 版本变没变、`InstallLocation`（注册表）指向哪、
   `HKCU\Software\简单ERP\LastAppDir` 指向哪；`config.json` 哈希不变不等于装对了；
→ 装错目录的善后：先用正确 `/DIR` 重装一次（会重写卸载键与 `LastAppDir`），
   再手工删掉误写在仓库根的 `_internal/`、`unins000.dat`、`unins000.exe`、`简单ERP.exe`。

## 原生端视觉层级：卡片必须有投影（2026-10-22）

用户报「颜色太单调了，分不清楚主次，看哪里都一样」。根因不是配色丑，是**层级缺失**：

- 网页版 `.card` 有 `--card-shadow`，原生 `ErpCard` 只有一道 `border alpha 0.07`。
- `bg #FBFBFD` 与 `surface #FFFFFF` 亮度差不到 2% → 卡片、卡片间隙、页面背景三者视觉等价。
- 结果：整屏是一张平铺的白纸，眼睛找不到结构。

修法与约定：
1. `ErpColors` 新增 `cardShadow`，`ErpCard` 默认 `elevated = true` 加 `shadow(6.dp)`。
2. `ErpColors` 新增 `debt`（暖色）专给欠款金额用 —— **不要用 danger**，danger 是「出错了」语义。
3. 列表行必须三级：主标题 16sp Medium / 金额 19sp SemiBold + debt 色 / 辅助 11.5sp muted。
4. 分段选择器用 `SegmentedChips`（并排、选中项标蓝），**不要用「文字互变的单开关」**：
   单开关在「未激活」状态看起来跟普通按钮一样，用户不知道当前选的是哪个（用户 2026-10-22 报的第一个问题）。
5. 筛选 chip 未选中态用 `soft` 灰底，不用 `surface` 白底 —— 白底落在浅灰页面上没有边界。

## 离线缓存：先给缓存，再走网络（2026-10-22）

原实现「先网络、超时才回落缓存」，电脑关机时切页面要等满 connectTimeout。
实测：**8.7 秒**白屏转圈。

修法（`AppViewModel.load`）：
- 新增 `publish` 参数，命中缓存时**立刻**推 `UiState.Ready(fromCache = true)`，界面秒开 + 离线横幅；
- 网络请求继续跑，成功了覆盖成在线数据；
- `UiState.Loading` 只在 `!is UiState.Ready` 时才设，否则每次切 tab 先闪骨架，有了缓存反而更卡；
- `connectTimeout` 8s → 2500ms（局域网内超过 2.5s 基本就是连不上，多等无益）；
  `readTimeout` 保持 15s（那是服务端在算，大数据量需要时间）。

实测结果：**8.7 秒 → 1.05 秒**。

## 商品页排序（2026-10-22）

- 商品没有「欠款」概念，所以第二档排序用**最低价**（`sort=price`），与客户页「拼音/欠款」结构对应。
- 无专属价的商品（`MIN(cp.price_cents) IS NULL`）**永远垫底**，两档排序都垫。
- 空拼音垫底那一项必须固定 `ASC`，**不能跟 direction 翻转**：写成 `f"p.pinyin_initials = '' {direction}"` 时，
  倒序会把空拼音翻到最前面，看起来像排序坏了。
- 排序条件必须进缓存键（`products:$sort:$order:$q`），否则离线时看到另一个排序的结果，像「排序没生效」。

## 排序切换要回顶（2026-10-22 用户要求）

客户页/商品页都加了 `rememberLazyListState()` + `LaunchedEffect(sortDebt, descending) { scrollToItem(0) }`。
不回顶的话，用户滚到第 30 行再切排序，看到的是新排序的第 30 行 —— 完全不知道排的是哪一段。

## 圆角矩形描边（2026-10-22）

用户要求「全加黑色描边」。实现要点：
- `ErpColors` 新增 `outline`：浅色 `#000000`，深色 `#FFFFFF`。
- **深色模式不能也写纯黑** —— 深色背景上的黑描边等于没画，用户会以为没生效。
- 已覆盖：`ErpCard` / 搜索框 / `SegmentedChips` / `OrderChip` / `PrimaryButton` / `FilterChip` / 净额卡 / tab 条分隔线。

## adb 点击坐标：别按截图比例换算（2026-10-22 又踩一次）

模拟器 `wm size` 是 `Physical 1080x1920` / `Override 945x2100`。
`screencap` 出的是 **945×2100**（override），但 `input tap` 走的是**物理 1080×1920**。
按截图比例换算会系统性偏掉，表现为「点了没反应」，很容易误判成 App 的 bug（这次连点三次日期面板都以为坏了）。

**正确做法：直接问系统要真实坐标**，不要算：
```bash
ADB shell uiautomator dump //sdcard/ui.xml
ADB shell cat //sdcard/ui.xml | tr '>' '\n' | grep -oE 'text="确定"[^/]*bounds="[^"]*"'
# 再取可点击容器（TextView 的 clickable 是 false，点它没用）：
ADB shell cat //sdcard/ui.xml | tr '<' '\n' | grep 'clickable="true"' | grep -oE 'bounds="[^"]*"'
```
点 bounds 的**中心**。注意 Git Bash 会把 `/sdcard` 改写成 `D:/app/Git/sdcard`，必须写 `//sdcard`。

## 原生端详情页层次（2026-10-22）

用户连报两次「没有层次、颜色字体大小没主次」。规律很明确：**问题不在配色，在「每一块都长得一样重」**。

两条可直接复用的规则：
1. **一张卡内分行，不要一项一张卡。** 明细 5 项 = 5 张等大白卡，跟抬头卡同权 → 整页一片方块。
   改成一卡多行 + 发丝线分隔，「明细」才成为一个整体块，跟抬头/合计形成三段结构。
2. **每页只能有一个「最大字号」。** 单据详情定为：金额 27sp Bold > 客户名 21sp SemiBold > 单号 12.5sp muted。
   原来客户名 18sp 对明细 14sp，差距太小，等于没有主次。

## tab 条不要分隔线（2026-10-22）

之前给 tab 条顶部加过 `outline` 1px 线，浅色下像硬边框、深色下像亮线，两种模式都抢眼，用户要求删掉。
tab 条与内容区的分界靠 tab 条自己的底色。**别再往这里加线。**

## tab 选中态用「胶囊底色」而不是细横线

原来 3dp 细横线 + 文字变蓝，用户报「不明显」。5 个 tab 平分屏宽后每格很窄，细线几乎看不见。
改成选中项整格铺 `primarySoft` 圆角底 + 文字 `primary` SemiBold：**面积差异**比线条好认得多。

## 详情页折叠：默认 3 条 + 原地展开（2026-10-22 用户要求）

- 三段（最近单据 / 专属价 / 最近流水）各用一个 `rememberSaveable` 布尔，**互不联动**。
  一个总开关会让专属价那几十行一起铺开，把流水顶到很下面。
- 展开只改本地布尔，**不发请求**，所以不回顶、不闪屏（用户明确要求「不要回顶刷新」）。
- 「展开」放在**段末**而不是段首：段首右侧已有「看全部」（跳页），两个操作挤一行会混淆。

## 「展开」按钮出不来 = 后端切太早（2026-10-22 实测 bug）

客户详情页「展开全部」一直不显示。原因不在前端：
`readonly_queries.customer_detail(document_limit=3)` 硬切 3 条，
前端条件是 `data.documents.size > COLLAPSED_ROWS`（3 > 3 = false），永远不成立。

**规则：折叠是显示层的事，接口要给足数据。**
- 后端 `document_limit` 3 → 20（上限而非无限，防老客户几千张单撑大响应）。
- 前端 `take(3)` + 「展开全部 N 条」按钮。
- `tests/test_mobile_queries.py` 里那条 `<= 3` 断言同步改成 `<= 20`（它测的是旧契约）。

## 详情页「还是太丑」的两次迭代（2026-10-22）

第一版只调了字号和卡内分行，用户仍说丑。第二版才抓住真正原因：
**同宽白卡上下叠 = 没有主次**。可复用做法：

- 客户详情：余额卡和电话/地址卡**合并成一张**。上半铺淡色底（欠款=暖色/多付=红/结清=灰）
  放 32sp Bold 余额；下半白底放联系方式，标签做成灰底小方块。
  低频信息（期初、本期变动）压成右上角小字，不跟主数字抢位置。
- 单据详情：抬头卡按单据类型着色（sale 蓝 / return 红 / purchase 琥珀，alpha 0.06）；
  明细行加 20dp 序号徽章（长单子防看串行）；合计改成「本单合计」小标题 + 25sp Bold 的色块。

## 别再自己发明列表样式 —— 先读网页版定稿（2026-10-22 反复被骂「丑」的根因）

用户连说三次「丑」。我一直在调字号、加投影、加底色，但**结构是错的**，怎么调都救不回来。

真正的根因：**网页版早就定稿了，原生端从没照做。**

| | 网页版（定稿） | 原生端（我做的） |
|---|---|---|
| 列表结构 | **一整张 card + 内部 row + 发丝线分隔** | 每行一张独立卡片 |
| 商品行 | 两行：名字 / 蓝色价格药丸 + N 个客户 | 三行：名字 / 客户数 / 价格悬空 + 单位 |
| 客户行 | 名字 + 拼音·电话 / 金额 / chevron | 名字 + 电话 / 金额 + 欠款徽章 |
| 控件描边 | 透明或极淡 | 纯黑 1px（像线框图） |

「每行一张卡」的后果：几十张**一样高、一样白、一样圆角**的方块排下去是一堵墙，
眼睛没有任何落点。这就是「丑」的本质 —— 不是配色，是**节奏**。

**规则：改原生端 UI 前，必须先读对应的网页版模板和 CSS。**
- 列表：`app/erp/templates/mobile/{customers,orders,products}/list.html`
- 样式：`app/erp/static/css/mobile.css`（`.row` `.p-name` `.price-pill` `.card`）
- 定稿注释都写在模板里（「2026-10-18 用户定稿」），照做即可，不要重新设计。

## 黑色 1px 描边 = 线框图观感（2026-10-22）

用户先说「全加黑色描边」，我加完发现整屏像没做完的线框图，用户随后要求删掉。
最终结论：**黑描边不要用在控件和卡片上**。控件用 `colors.border`（极淡）或靠底色区分，
卡片靠投影。`outline` 这个 token 目前只在必要时用，别默认套。

## 扫码「对着电脑扫不出来、相册能扫出来」（2026-10-22 真机反馈）

**根因不是解析器，是相机对焦没真正生效。** 这里记两处**曾写错的结论**，别照着错的思路改：

- ❌ 曾写「zxing 的 `CameraSettings` 默认 `autoFocusEnabled=true` 但 `continuousFocusEnabled=false`，
  只对焦一次就停了」——**这个描述不准确**，照着它写 `isContinuousFocusEnabled=true` +
  `isAutoFocusEnabled=false` 是**无效的**（v4.6.5 就这么写的，实测 logcat 仍是 `[auto]`）。
- ✅ 真正有两层坑（2026-10-22 反编译 `zxing-android-embedded 4.3.0` 字节码 + logcat 确认）：
  1. **两个 setter 会互相覆盖 `focusMode`**：`setContinuousFocusEnabled(true)` → `focusMode=CONTINUOUS`；
     紧接着 `setAutoFocusEnabled(false)` → `autoFocusEnabled=false` 且 **`focusMode=null`**。
     而 `CameraConfigurationUtils.setFocus()` **只看 `focusMode` 枚举**，不是 CONTINUOUS 就只请求 `auto`。
     → 正确写法：直接 `setFocusMode(CameraSettings.FocusMode.CONTINUOUS)`，不要用那两个布尔开关。
  2. **必须在 `captureManager.initializeFromIntent(...)` 之后设**：该方法内部会
     `new CameraSettings()` 并 `BarcodeView.setCameraSettings(...)`，覆盖掉之前在 `onCreate` 里设的值。

**验证判据（不必真机）**：logcat 里
`CameraConfiguration: Requesting focus mode value from among: [...]` 从 `[auto]` 变成
`[continuous-picture, continuous-video, auto]`，且 `Can set focus mode to: continuous-picture`、
`AutoFocusManager: Current focus mode 'continuous-picture'`。
**注意模拟器相机能力**：默认虚拟相机支持 `continuous-picture`；`-camera-back virtualscene` 只支持 `infinity, auto`，
所以在 virtualscene 下即便代码对了也只会回落 `auto` —— 别把「virtualscene 回落」误判成「修复没生效」。

## 扫码提示文字别用 zxing 自带的 status view（2026-10-22）

`zxing_status_view` 在库的布局里是 `layout_gravity="bottom|center_horizontal"` 硬编码，
永远贴屏幕最底边，离扫描框很远。要放到框下方就自己加一个 TextView，
用 `barcodeView.barcodeView.framingRect.bottom` 定位 —— **它已经是 View 坐标系**
（ViewfinderView 直接拿它 `canvas.drawRect`），不要再乘预览比例，多乘一次会把提示
推到屏幕中间、和相册按钮重叠（踩过）。相机起来前 framingRect 是 null，要等
`onGlobalLayout` 再算。

## 断网时三级页面「连不上电脑」= 这单从没联网打开过（2026-10-22）

缓存是**按接口逐条**落的：列表页缓存了，不代表里面每一张单据详情也缓存了。
用户从「最近单据」点进去，那张单可能一次都没被联网打开过 → 没缓存 → 断网必失败。

修法：`openDocument` 失败时用**列表里已有的摘要**降级渲染（`fallbackDocumentDetail`），
单号/日期/客户/金额/类型全在摘要里，只缺明细行；页面明确写「明细需要联网才能看」。
不要显示成「单据不存在」，也不要假装明细为空。

## 配对态丢失的坑（2026-10-22）

配对态是 **cookie**（`simple_erp_cookies` SharedPreferences，键是 `host:port`），
服务端设备登记在 `<data_root>/data/mobile_devices.json`。两边一旦不同步
（比如服务端那条设备被删了、或换了 IP/端口），App 里 cookie 还在但服务端不认，
表现是**每次启动都退回配对页**，而设置页看着一切正常。
排查命令：`adb shell su 0 cat /data/data/com.simpleerp.mobile/shared_prefs/simple_erp_cookies.xml`
拿到 `device_id:token`，再用 `mobile_pairing.device_from_cookie()` 验证。

## 设置页「已配对设备 0 台」不刷新（2026-10-22）

设置页是**服务端渲染**，HTML 只在打开那一刻生成一次。店主开着这个页面、拿手机
扫码配对 —— 服务端 `mobile_devices.json` 已经写进去了，但页面不会自己变，
「已配对设备 0 台」一直挂着，看起来像配对失败。

修法：加 `GET /settings/mobile/devices` 返回 JSON，前端每 4 秒拉一次，
**只在设备签名变化时才重绘**（否则每 4 秒重建整张表，鼠标选中的文字会被清掉）。
排查时要分清「服务端没记」和「页面没刷」：先 `mobile_pairing.device_count()`，
数字对得上就一定是页面的事。改完后端**必须重启桌面端进程**，否则路由不生效（404）。

## 照片扫码失败：MultiFormatReader 要给 hints（2026-10-22）

`MultiFormatReader()` 不带参数时是「所有格式都试」，在没有 TRY_HARDER 的情况下
反而容易在噪声上失败。修法是多轮尝试：只认 QR_CODE → 只认 QR_CODE + TRY_HARDER →
全格式 + TRY_HARDER → 图放大 2 倍再试（截图缩放过、码只有 100px 时靠这步救）。
