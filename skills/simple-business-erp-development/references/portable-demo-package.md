# 绿色版自带演示数据的打包规则（2026-10-14 用户决策）

**决策**：绿色版目录要自带 200 条演示业务数据（打开即用）；安装包与 ZIP 是给门店/别人的正式程序，**不得**含演示数据。

## 五步流程（`打包Windows安装包.bat`）

1. `简单ERP.spec`（正常入口）one-folder → `build\clean-<版本>\简单ERP`（交付用干净产物）
2. Inno Setup，显式 `/DSourceDir=<干净产物>` → `dist\simple-erp-setup-<版本>.exe`
3. `make_release_zip.py --source "<干净产物>"` → `dist\simple-erp-windows-<版本>.zip`
4. `简单ERP-demo.spec`（入口 `packaging/demo_entry.py`）→ `dist\local-<版本>\简单ERP`
5. `seed_demo_data.py --data-root <绿色版>\demo_data --documents 200 --config-path <绿色版>\config.json`

## 硬约束（违反会静默产出错误资产）

- **顺序**：写演示数据必须是最后一步。ZIP 早于它生成；`make_release_zip.py` 也会在写盘前拒绝 `demo_data` / `*.db`，这是第二道闸。
- **绿色版与安装包/ZIP 不是同一份 EXE**：绿色版入口把数据根强制指向包内 `demo_data`（与「文档\简单ERP数据」物理隔离）；安装包/ZIP 用正常入口。改打包时不要把两者混用。
- **每轮打包前先 `rmdir /s /q` 上一轮的 `%CLEANDIR%` / `%GREENDIR%`**：`seed_demo_data.py` 见到已存在的库会以「目标库已存在」拒绝执行，实测第二次打包会卡在第 5 步。
- **`packaging/default_config.json` 要与 `CONFIG_DEFAULTS` 同步**：它是装进包里的出厂配置，新增配置项（如 `slip_size`）时别忘补键，否则包内配置与代码默认值不一致。

## 验收（必须真做，不能只看构建成功）

- 绿色版：包内 `demo_data\data\erp.db` 存在；四类单据**全表**计数 = 200（`orders` + `purchase_orders` + `purchase_return_orders`，只数 `orders` 会得到 125 而误判）；`PRAGMA integrity_check` = ok；包根配置是演示店铺名。
- 绿色版功能：真启动 `简单ERP.exe` → `/health` 的 `data_root` 必须指向包内 `demo_data`，单据管理页能列出演示单。
- 干净产物与 ZIP：`*.db` / `demo_data` / 演示说明 **0 个**；ZIP 内 EXE 应与干净产物 EXE **同哈希**（证明来自干净构建）。
- 两份 EXE 哈希必须**不同**（证明入口确实分开）。

## 坑

- **验收动作会弄脏绿色版**：启动 EXE 会在 `demo_data` 里重建 `-wal`/`-shm` 并写日志；`seed` 生成时是干净的（manifest `problems: []`），脏是验收造成的。交付前用**读写**连接 `PRAGMA wal_checkpoint(TRUNCATE)` 后正常关闭（只有读写连接关闭才会删边车，`mode=ro` 会再造出 `-shm`），并清掉 `demo_data\logs\*.log`。
- **改 `.bat` 必须保住 CRLF**：用 `read_text()`（通用换行把 CRLF 归一成 LF）再 `write_text(newline="")` 写回会把整个脚本写成 LF，cmd.exe 会解析失步。正确写法是写盘时 `newline="\r\n"`，或直接按字节改。契约测试见 `tests/test_windows_packaging.py`。
