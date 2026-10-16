# 踩过的坑

## 备份与恢复的范围

`utils/backup.py:create_backup` 覆盖 `erp.db` + `config.json` + `product_images/` + `legacy_archive/`。
恢复已实现 `restore_backup`：**默认不动 `config.json`**，恢复前自动建 `pre_restore` 备份。

## 客户与供应商共用一张表

`customers` 表同时存客户和供应商，`name` 有 UNIQUE 约束，**软删除的记录也占名**。
→ 判断「能否新建/导入」时不能只看有效记录，必须按含软删除的唯一性来判断。
（业务规则见 `docs/需求/库存成本与对账业务契约.md`；导入细节见 references/`customer-analytics-and-master-data-import.md`。）

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

## 发布提交要用显式 pathspec

工作区常驻「前序会话/用户自己的未提交批次」（`.gitignore`、几个 `.bat`、未跟踪的 `memory/`、
已 staged 的 68 个 `开发短计划/` 删除）。`git commit -a` 会把它们一起卷进发布提交；
一律 `git commit -- <明确路径…>`，并在提交后核对 `git status` 里那批仍然原样留着。

