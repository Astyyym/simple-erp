# Windows 桌面版打包说明

目标：生成用户可直接双击运行的 Windows 桌面版，不要求日常使用者打开终端、安装 Python 或连接互联网。

当前有两种分发形态：

| 形态 | 产物 | 适用 |
|---|---|---|
| **安装包**（推荐给门店） | `dist\simple-erp-setup-vX.Y.Z.exe` | 双击安装到 `%LOCALAPPDATA%\Programs\简单ERP`，开始菜单/桌面图标，可正常卸载；per-user，不需要管理员 |
| 绿色包（免安装） | `dist\local-vX.Y.Z\简单ERP\` + `dist\simple-erp-windows-vX.Y.Z.zip` | 解压即用，适合 U 盘/临时电脑 |

两种形态共用同一份 PyInstaller one-folder 产物；安装包只是在其之上加了一层安装器。

## 1. Windows 打包环境

- 当前默认业务环境为 Windows 11。构建脚本会创建 `.venv-win`，优先使用 `py -V:Astral/CPython3.11.15`，失败时尝试 `py -3.14`；不要用 WSL Python 打 Windows EXE。

优先直接运行 `打包Windows桌面版.bat`：脚本会在 `.venv-win` 不存在时创建环境（先尝试 `py -V:Astral/CPython3.11.15`，再回退 `py -3.14`），并安装/检查依赖。已有 `.venv-win` 时不要重复创建。只有手动准备独立的 Python 3.11 环境时才使用下列命令：

```bat
py -3.11 -m venv .venv-win
.venv-win\Scripts\python.exe -m pip install --upgrade pip
.venv-win\Scripts\python.exe -m pip install -r requirements.txt
.venv-win\Scripts\python.exe -m pip install pyinstaller pywebview
```

## 2. 打包

### 2.1 安装包（推荐）

```bat
打包Windows安装包.bat
```

该脚本会先构建 one-folder 程序目录到 `dist\local-v<版本>\简单ERP\`，再调用 Inno Setup 编译器生成 `dist\simple-erp-setup-<版本>.exe`。

需要 Inno Setup 6（只在本机构建时需要，不进交付包）：

```bat
winget install --id JRSoftware.InnoSetup
```

安装脚本 `packaging\installer\简单ERP.iss` 的硬约束（由 `tests/test_installer_packaging.py` 守护）：

- **per-user 安装**：`DefaultDirName={localappdata}\Programs\简单ERP`、`PrivilegesRequired=lowest`、禁用提权覆盖。**不得改成 Program Files**——程序把可写 `config.json` 放在 EXE 旁（`erp/config.py: runtime_root()`），装进受保护系统目录会让非管理员保存设置失败。
- **升级不覆盖店铺设置**：`config.json` 单独一条 `onlyifdoesntexist uninsneveruninstall`。
- **不碰业务数据**：`[Files]`/`[UninstallDelete]` 只覆盖 `{app}`；不打包 `*.db`/演示库/日志。
- **版本单一真源**：ISPP 从仓库根 `VERSION` 读，脚本内不写死版本号。
- 简体中文安装界面需要 `packaging\installer\ChineseSimplified.isl`（Inno 发行包不带中文语言文件）。

### 2.2 绿色包（免安装）

```bat
打包Windows桌面版.bat
```

当前 PyInstaller one-folder 产物目录（本轮为 `dist\local-v3.6.0\简单ERP\`）：

```text
dist\local-vX.Y.Z\简单ERP\
  简单ERP.exe
  config.json
  _internal\
```

打包脚本也可能生成版本化 ZIP。实际文件名及版本以本次打包脚本输出为准，常见示例：

```text
dist\simple-erp-setup-vX.Y.Z.exe
dist\simple-erp-windows-vX.Y.Z.zip
```

仅本地存在 ZIP/EXE 不能证明其已上传 GitHub Releases；推送源码、构建包和发布 Release 是不同状态。

本轮 `v2.0.0` 为独立新目录 `dist\local-v2.0.0\简单ERP\` 和 `dist\simple-erp-windows-v2.0.0.zip`。由当时Windows源码fresh构建，未覆盖上述常规目录或旧包。ZIP完整解压后使用；根配置与冻结内置配置都来自 `packaging/default_config.json`，不是本机config。Windows产品版本保留精确 `v2.0.0`，数字文件版本为 `2.0.0.0`。

当前 `v3.6.0` 在 v3.5.0 之上新增**安装包形态**：产物为 `dist\local-v3.6.0\简单ERP\`、`dist\simple-erp-setup-v3.6.0.exe` 和 `dist\simple-erp-windows-v3.6.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。安装包为 per-user 安装（`%LOCALAPPDATA%\Programs\简单ERP`），不需要管理员；`config.json` 升级不覆盖、卸载保留；业务数据目录零接触。Windows 产品版本 `v3.6.0`，数字文件版本 `3.6.0.0`。

当前 `v3.5.0` 在 v3.4.0 之上交付「筛选无刷新、侧栏导航归属、动作标签统一与数据分析图型收口」整批（六页筛选/翻页就地刷新不再闪烁回顶 + 共享 `page-refresh.mjs`、侧栏高亮改按路由 owner 判定、查看/编辑/打印词统一、条件不可用的「编辑」改置灰+原因、成本环形切换与柱状生长动画），产物为 `dist\local-v3.5.0\简单ERP\` 和 `dist\simple-erp-windows-v3.5.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。Windows 产品版本 `v3.5.0`，数字文件版本 `3.5.0.0`。本版已推送 GitHub 并发布 Release `v3.5.0`（ZIP 资产下载回本地与构建产物字节级一致）；桌面 EXE 原生窗口/OS 鼠标矩阵、实体打印与浅/深/跟随系统三主题矩阵仍未验。

当前 `v3.4.0` 在 v3.3.0 之上交付「EXE 启动速度优化」整批（惰性导入 `weasyprint`/`openpyxl`/`pypinyin`/`PIL` + 桌面壳品牌 splash 先行与抢跑修复），产物为 `dist\local-v3.4.0\简单ERP\` 和 `dist\simple-erp-windows-v3.4.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。Windows 产品版本 `v3.4.0`，数字文件版本 `3.4.0.0`。本版已推送 GitHub 并发布 Release `v3.4.0`（ZIP 资产下载回本地与构建产物字节级一致）；真实桌面观感（splash 无闪烁/重复双击）待用户实机确认。

当前 `v3.3.0` 在 v3.2.0 之上交付「旧数据迁移承接」整批（备份与恢复、导入扩列与两段式预检、期初成本来源、区间两端余额、档案来源标记与零建档起步、店铺信息引导与顶栏实时钟、基础资料列表页三段式重排、品牌区英文副标题移除），产物为 `dist\local-v3.3.0\简单ERP\` 和 `dist\simple-erp-windows-v3.3.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。Windows 产品版本 `v3.3.0`，数字文件版本 `3.3.0.0`。

当前 `v3.2.0` 在 v3.1.0 之上合并交付产品审查修复第二轮与筛选卡三段式重排，产物为 `dist\local-v3.2.0\简单ERP\` 和 `dist\simple-erp-windows-v3.2.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。Windows 产品版本 `v3.2.0`，数字文件版本 `3.2.0.0`。

当前 `v3.1.0` 为首次推送 `main` 并创建 GitHub Release 的版本，产物为 `dist\local-v3.1.0\简单ERP\` 和 `dist\simple-erp-windows-v3.1.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。Windows 产品版本 `v3.1.0`，数字文件版本 `3.1.0.0`。

## 3. 安装与启动

**安装包**：双击 `simple-erp-setup-vX.Y.Z.exe`，按向导下一步即可（安装前可读「安装说明」页）。程序装到 `%LOCALAPPDATA%\Programs\简单ERP`，开始菜单出现「简单ERP」，勾选后桌面也有快捷方式；卸载走「设置 → 应用」或开始菜单的「卸载 简单ERP」。

**绿色包**：将整个 `dist\local-vX.Y.Z\简单ERP` 文件夹复制到目标 Windows 电脑，例如 `D:\简单ERP\`，然后创建 `简单ERP.exe` 的快捷方式。不要只复制 EXE，one-folder 依赖 `_internal`。

程序运行本地服务，桌面窗口访问 `127.0.0.1:5000`。当前版本永久免登录，任何能访问该端口的人都可能操作数据；不得把服务开放给局域网或公网。

## 4. 数据目录

| 项 | 当前行为 |
|---|---|
| 新装桌面版默认数据根 | `%USERPROFILE%\Documents\简单ERP数据` |
| 业务数据库 | `{数据根}\data\erp.db` |
| 路径记忆 | `%LOCALAPPDATA%\简单ERP\data_location.json`，只记录数据根路径 |
| 设置 | 设置页显示当前/默认路径；桌面版可选文件夹并迁移数据 |
| 源码运行 | 默认数据根为项目运行目录，通常是仓库根目录 |

数据根可以由用户在设置中更改。迁移前备份；目标目录已有非空数据库时程序会拒绝覆盖；迁移后旧目录仍保留，不要未经确认删除。

## 5. 备份与升级

升级前至少备份设置页显示的数据根目录下的 `data` 文件夹。退出程序后，仅替换程序文件（EXE、`_internal` 等）；不要用构建包中的空库覆盖用户现有的 `data\erp.db`。

**用安装包升级**：直接运行新版本安装包即可，不必先卸载。安装器会覆盖程序文件，但不会覆盖用户改过的 `config.json`（店铺名、打印偏移、主题等），也不会碰业务数据目录。卸载同样只删程序文件。

如果旧安装把数据放在 EXE 旁边的 `data\`，整目录覆盖风险更高；应先确认实际数据根目录。绿色包升级不需要通过卸载程序完成。

`v2.0.0` 首次连接旧库会执行内置schema升级，升级前需备份整个数据根并保留旧程序的config.json。通用默认配置不能覆盖旧店铺/打印设置；旧历史库存/成本不自动补造，正式期初启用需独立确认，不能升级后直接用旧程序打开新库。本轮只在隔离虚构与空数据上验证，不迁移或投用正式库。

## 6. 打印

- 销售单、退货单、客户货款汇总 PDF 和设置页样张默认使用 A4 竖版（210×297mm）；销售/退货单内容为正向竖版排版，不再放入旧 241×140mm 单据区。
- 打印驱动建议选 A4 纵向、实际大小/100%，关闭“适合页面/缩放”。历史 `NantianPR-LQ` 记录曾提供 A4 纸型，但当前驱动版本和目标电脑选项未重新核验。
- 设置页的横纵偏移和缩放校准销售/客户退货、拿货/退拿货单及设置样张；账款汇总和往来交易对账 PDF 不使用这些偏移。
- 先检查 HTML/PDF 预览，再在目标电脑/打印机实打校准。生成 PDF 或自动化测试通过不能替代目标打印机人工验收。

## 7. 证据与发布状态

每次正式交付分别记录：

1. 测试与源码运行结果；
2. Windows EXE 是否从隔离目录真实启动、`/health` 版本是否正确、业务页是否可用；
3. ZIP 是否正确生成及其文件名/大小；
4. Git 是否提交/推送；
5. GitHub Release 是否存在且包含预期资产。

不得把其中一项推断成其他项已完成。打包流程和 Windows 中文路径冒烟细节见项目技能 references 索引。

`v2.0.0` 本轮证据：529项Python/24项前端、fresh EXE持有回环5000且health精确版本与隔离根、21组分析交互/主题宽度、7组HTTP与6份逐页A4 PDF、原生WebView2真实Python桥及9组路径初启/重启、随包通用配置空库初始化与样张通过。PDF关键GTK/Pango模块从随包_internal加载；ZIP逐文件SHA256/CRC及保护元数据/进程/端口清理通过。原生窗口完整视觉/OS鼠标矩阵、保存对话框人工选择/取消、目标打印机、独立新电脑/完全断网环境仍未验，不据此宣称全平台或离线可靠。Git提交、推送、tag、Release均未执行。
