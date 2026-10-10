# Windows 安装包（Inno Setup per-user）

本文件记录简单ERP **安装包形态**的当前规则。绿色包（PyInstaller one-folder + ZIP）规则见
[`windows-native-exe-packaging.md`](./windows-native-exe-packaging.md)；两者共用同一份 one-folder 产物。

## 为什么必须 per-user

程序把可写 `config.json` 放在 **EXE 旁边**（`app/erp/config.py` 的 `runtime_root()` / `writable_config_path()`）。
装进受保护的系统目录（Program Files）后，非管理员用户保存设置会直接失败。

因此安装脚本的硬约束是：

- `DefaultDirName={localappdata}\Programs\简单ERP`
- `PrivilegesRequired=lowest` + `PrivilegesRequiredOverridesAllowed=`（空 = 禁止切到提权模式）

**不要把安装位置改成 Program Files。** 真要装那里，必须先把配置写入路径从「EXE 旁」改成
`%LOCALAPPDATA%` 之类可写位置——那是动业务代码的独立改造，不属于打包层。

## 安装包与用户数据的关系

| 项 | 规则 |
|---|---|
| `config.json` | 单独一条 `Flags: onlyifdoesntexist uninsneveruninstall` → 升级不覆盖用户店铺设置，卸载保留该文件 |
| 程序文件 | `Source: "{#SourceDir}\*"`，`Excludes: "config.json"`，`ignoreversion recursesubdirs createallsubdirs` |
| 业务数据 | 完全不在安装范围。数据根默认 `%USERPROFILE%\Documents\简单ERP数据` |
| `[UninstallDelete]` | 只写 `{app}` 下的安装残留（`_internal`、主 EXE）；**不得**出现 `Documents` / `简单ERP数据` / `erp.db` |
| 卸载后 | 程序目录会残留 `config.json`（+ 目录本身），卸载完成弹窗说明数据位置 |

`tests/test_installer_packaging.py` 用文本断言守护上述四点，包括「`[UninstallDelete]` 段不得提及数据目录」。

## 版本单一真源

安装脚本用 ISPP 读仓库根 `VERSION`，脚本里**不写死版本号**：

```
#define VerFile AddBackslash(SourcePath) + "..\..\VERSION"
#define AppVersion Trim(FileRead(FileOpen(VerFile)))
#define NumericVersion Copy(AppVersion, 2) + ".0"   ; vX.Y.Z -> X.Y.Z.0
```

- `AppVersion={#AppVersion}` → PE `ProductVersion` 自动继承（`vX.Y.Z`）
- `VersionInfoVersion={#NumericVersion}` → PE `FileVersion` 为 `X.Y.Z.0`
- 注意 ISPP 没有 `Length()`/`StringChange()`；用 `Copy(s, 2)` 去前导 `v` 最省事。
- `StrToVersion()` / `PackVersionComponents()` 生成的 64 位整数直接给 `VersionInfoVersion` 会被判非法，别用。

## 中文界面

Inno Setup 6 发行包的 `Languages\` 目录**不含简体中文**（只有 29 个非中文语言文件）。
`packaging/installer/ChineseSimplified.isl` 从官方 `jrsoftware.org/files/istrans` 指向的
`Files/Languages/ChineseSimplified.isl` 取得，随仓库提供，安装脚本用
`MessagesFile: "compiler:Languages\ChineseSimplified.isl"` 引用。

## 构建

```bat
打包Windows安装包.bat
```

脚本**一次出齐三种交付产物**（发版标准，见 `AGENTS.md`「交付产物」）：

1. `dist\local-<版本>\简单ERP\` — 绿色版目录（PyInstaller one-folder）
2. `dist\simple-erp-setup-<版本>.exe` — 安装包（Inno Setup）
3. `dist\simple-erp-windows-<版本>.zip` — 交付 ZIP（`packaging/make_release_zip.py`）

流程：建/复用 `.venv-win` → PyInstaller one-folder → 补根 `config.json`（手动 `--distpath` 不会生成）
→ 找 `ISCC.exe`（先 `%LOCALAPPDATA%\Programs\Inno Setup 6`，再 `%ProgramFiles(x86)%`、`%ProgramFiles%`）
→ 编译安装包 → 生成 ZIP。

首次需要安装编译器：`winget install --id JRSoftware.InnoSetup`（只在本机构建时需要，不进交付包）。

**两个实测坑**：

- 用 `.venv-win\Scripts\python.exe -m PyInstaller`，不要直接调 `pyinstaller.exe`——MSYS 下后者可能完全没有输出。
- ZIP 必须由脚本生成，不要手工打：手工那次容易漏掉、也容易把业务数据混进去。`make_release_zip.py`
  在写盘前就拒绝 `.db`/`.db-wal`/`.db-shm`/`.log`/`demo_data`/`backups`/`imports`/`temp_pdf`/`legacy_archive`
  条目，并断言顶层目录唯一为 `简单ERP/`。

**只保留当前版本的产物**：`dist/` 里每版都留一份会堆出多个同名 `简单ERP.exe`，用户会点错并误以为
「安装包不走向导」。新版本构建成功后删掉旧 `local-v*` 目录与旧 ZIP。

## 验收方法（可脚本化）

安装器支持静默参数，能在隔离目录做真实安装/重装/卸载三段验收，无需人工点击：

```bash
SETUP="dist/simple-erp-setup-vX.Y.Z.exe"
TARGET='C:\...\scratch\instprobe'          # 原生 Windows 路径，不能是 MSYS /tmp

"$SETUP" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/DIR=$TARGET"
# 1) 程序文件落地、无 *.db
# 2) 改 $TARGET/config.json（模拟店铺设置）
"$SETUP" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/DIR=$TARGET"   # 重装=升级
# 3) cat $TARGET/config.json 必须仍是改动后的内容
"$TARGET/unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
# 4) 卸载后只剩 config.json；数据根目录与 erp.db 字节不变
```

再以隔离 `ERP_DATA_ROOT` 启动**已安装的** `简单ERP.exe`，核对 `/health` 版本与关键页面 200。
安装版仍是同一份 EXE，所以窗口/打印等验收结论与绿色包共用，不必重复。

**证据边界**：静默安装证明不了向导页文字、图标、开始菜单项在真实用户点击下的表现，
也证明不了非中文 Windows 与无 WebView2 机器；这些要如实标 `unverified`。

## 已知边界

- 绿色包与安装版同时运行会抢 5000 端口（`desktop_app.py` 的 `already_running` 分支会直接连已有实例）。
  两种形态不建议同时开。
- 安装器只做提示不阻断：检测到 `简单ERP.exe` 在运行会问是否关闭；缺 WebView2 只给官方下载地址，不静默联网。
- 卸载残留的 `config.json` 若被手动删除，重装后店铺名等设置回到出厂默认（数据不受影响）。
