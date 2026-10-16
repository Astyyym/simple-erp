# 踩过的坑

## 备份与恢复的范围

`utils/backup.py:create_backup` 覆盖 `erp.db` + `config.json` + `product_images/` + `legacy_archive/`。
恢复已实现 `restore_backup`：**默认不动 `config.json`**，恢复前自动建 `pre_restore` 备份。

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

