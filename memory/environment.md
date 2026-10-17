# 本机环境与本项目技术栈边界

## 运行环境

- 简单ERP **只在 Windows 跑**；WSL 仅通过盘映射访问同一仓库做可选交叉测试，不建第二套长期源码。
- 源码开发：`.venv\Scripts\python.exe app\app.py`；测试 `.venv\Scripts\python.exe -m pytest tests\ -q`
- 打包 venv：`.venv-win\Scripts\python.exe -m pytest tests\ -q`

## 本机数据根（先搞清哪个库在跑）

- 本机**正式运行库**是 `C:\Users\czk\Documents\简单ERP数据\data\erp.db`（冻结/桌面版默认位置，由 `%LOCALAPPDATA%\简单ERP\data_location.json` 记住），该库已是 schema v15。
- 项目根 `data/` 只是 2026-07 源码开发遗留的 v2 老库（13 客户 / 17 商品 / 21 单），**不是运行库**；2026-10-16 已归档为 `backups/legacy-v2-erp-20260714.db`（`backups/` 在 .gitignore 内，不随仓库发布），项目根 `data/` 现为空。
- 先确认再动手：`.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,'app');from erp import db;print(db.db_path())"`。

## PDF / 打印验证的本地边界

- 本机 `pdftotext` 是 **Xpdf 4.00**：只有 `-layout` / `-raw` / `-table`，**没有 `-bbox`**。
- 本项目 WeasyPrint 出的 PDF **抽不出中文**（缺 CJK ToUnicode，出来是 `�`）→ `-raw` 只能验顺序与数字，中文文案要靠 HTML 层断言或人工目视。
- `.venv-win` **没有 PDF 光栅化库**（pypdfium2 / fitz / pdf2image 都没有）→ 本地出不了 PNG，只能交 PDF。

## 主题同步机制（容易看到假缺陷）

- 主题同步脚本**只在页面加载时执行**。用 JS 手改 `data-ui-theme` **不会**同步 `data-bs-theme`，会看到假的「黑字」缺陷。
- 真实验证路径：改 `config` 的 `ui_theme`，或走设置页保存后**重新加载**页面。

## 本机 shell 边界

- bash（MSYS）里 `$TMPDIR` 解析为 `/tmp`，而原生 Windows 程序（python/node 等）打不开 MSYS 路径。
- 给原生工具读写的临时脚本要写显式路径：`%LOCALAPPDATA%\hermes\cache\scratch\...`（即 Hermes scratch 目录，bash 里是 `$TMPDIR` 的真实位置）。
- **在 bash heredoc 里给 Python 写 Windows 路径会把 `\a` 之类转义成控制字符**（`D:\app` 变成 `D:` + BEL + `pp`）。要么用 `write_file` 直接写文件，要么在脚本里用正斜杠 `D:/app/...`。

## 测试与端口

- `.venv-win` **没装 `pytest-timeout`**：`pytest --timeout=…` 直接报 `unrecognized arguments` 退出，连收集都到不了。防卡死只能靠 `ERP_PORT=5099` 避让 + 事后看进程。
- 跑全量前先确认端口：`netstat -ano | grep LISTENING | grep -E ":5000|:5099"`（用户机器上常有正在运行的桌面版占着 5000，不要要求他关程序）。
- 前端测试：`node --test tests/frontend/*.test.mjs`（当前 106 项）。**不要接 `| tail`**——实测会只剩 `stdin is not a tty` 一行；先重定向到文件再读。

## 安卓 APK 壳（`apk/`）与本机模拟器

- 工具链全在本机，不用另装：
  - JDK 21：`D:\app\Android\jdk21`
  - SDK：`D:\app\Android\Sdk`（platforms `android-36`、build-tools `35.0.0`/`36.0.0`、platform-tools）
  - Gradle 8.14.3 分发：`D:\app\Android\gradle-cache\wrapper\dists`
  - AGP 8.11.0 已缓存 → `--offline` 可构建
- 工程路径含中文，`apk/gradle.properties` 必须有 `android.overridePathCheck=true`，否则 AGP 以「project path contains non-ASCII characters」直接中止构建。
- **构建后先把 `apk/app/build/outputs/apk/release/app-release.apk` 复制到安装路径，再 `adb install`**。漏这一步装进去的还是上一版，每个修复都会显示「没效果」（2026-10-17 因此误判两轮）。
- **模拟器验收必须让用户看见（用户 2026-10-17 明确要求）**：跑 APK 测试时**打开 Android Studio**，从 Device Manager 启 AVD（`goal11_api36_x86_64`，AVD 目录 `D:\app\Android\avd`，需要 `ANDROID_AVD_HOME`）；不进 Android Studio 就至少用**带窗口**的 `emulator -avd <avd>`，**绝不加 `-no-window`**。用户要看得到过程和画面，不接受「无头跑完只给截图」。
- **验收结束不关闭模拟器**：不 `adb emu kill`、不卸载 App、不清 SharedPreferences 与已填地址，留给用户自己点着试。报告时说明「模拟器窗口留着，可以自己点」。
- 手机端页面依赖桌面端第二监听（默认 5001）：留着模拟器就要一并交代服务状态——指向用户自己的安装，或保留隔离实例并写清端口与数据根；不要留「能点开但一路兜底页」的假状态。
- adb：`D:\app\Android\Sdk\platform-tools\adb.exe`；模拟器访问宿主机用 `10.0.2.2`。
