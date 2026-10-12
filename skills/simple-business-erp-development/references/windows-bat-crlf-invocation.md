# 仓库脚本的行尾与编码（`.bat` 需 CRLF）

> **现存脚本**：`setup.bat`、`dev_start.bat`、`打包Windows安装包.bat`、`打包Windows桌面版.bat`。
> 2026-10-11 已删除 `启动系统.bat` / `启动系统.vbs` / `停止系统.bat` 及两个 WSL 版启动脚本
> （用户不再用它们启动，日常走安装版 `简单ERP.exe`）。源码服务改为直接
> `.venv\Scripts\python.exe app\app.py`。

## 症状（两种，取决于调用方式）

**A. 中文碎片被当成命令**（最常见的观察）：

```
'logs' 不是内部或外部命令
'olicy' 不是内部或外部命令          ← "ExecutionPolicy" 被切断
'ot' 不是内部或外部命令
'?setup.bat' 不是内部或外部命令     ← "请先运行 setup.bat" 被切断
'RP' / 'up' / 'o' / 'xist' 不是内部或外部命令
```

**B. 什么都不执行**（stdout 完全为空，退出码 0）——更危险，因为看起来「跑过了」。

两种的后果都是**步骤被静默跳过而非报错退出**。实测 `打包Windows安装包.bat`：
第 1 步补 `dist\...\config.json` 没执行，第 2 步 ISCC、第 3 步 ZIP 全被跳过，
只有 PyInstaller 那步成功（它是纯 ASCII 命令行，切碎后仍碰巧可用）。

## 根因一：`.bat` 是 LF-only（不是编码）

受控实验（同一含中文脚本，四种保存方式，`cmd.exe /c` 执行）：

| 保存方式 | 结果 |
|---|---|
| **UTF-8 + LF** | **失败** |
| UTF-8 + CRLF | 正常 |
| UTF-8 + BOM + CRLF | 正常 |
| GBK + CRLF | 正常 |

**编码不是问题，UTF-8 完全可用；LF-only 才是杀手。** cmd.exe 是行式解析器，期望 CRLF 作行终止。
`chcp 65001` 救不了：它改的是**输出**代码页，而文件在**读取阶段**就已解析错了。

## 根因一之二：`chcp 65001` + UTF-8 中文 + 全角标点（2026-10-11 新发现，与行尾无关）

**这是与「根因一」症状相同但成因不同的第二个坑，两者必须分开判断。**

症状与根因一几乎一样（`'xist' 不是内部或外部命令`、步骤被静默跳过、`PreprocessingError: Source file ... config.json does not exist`），
但**文件行尾是干净的 CRLF**（`CRLF=96, bare LF=0`），所以按根因一排查会找不到问题。

**受控实验（2026-10-11，`打包Windows安装包.bat`）**：

| 变体 | 结果 |
|---|---|
| UTF-8 + CRLF + `chcp 65001`（原样） | **✗ 解析错位**（`'xist'`、`'包' is not recognized`；补 config.json 被静默跳过） |
| UTF-8 + CRLF + `chcp 65001`，只把 `echo`/`rem` 行的全角标点换 ASCII | ✓ 通过（但末尾仍有零星切碎） |
| UTF-8 + BOM + CRLF | ✗ BOM 顶掉 `@echo off`，且仍错位 |
| **GBK + CRLF + `chcp 936`** | **✓ 端到端零错跑通三种产物** |
| UTF-8 + LF（v3.7.0 当时检出的状态） | ✗ 错位更严重 |

**机制**：`chcp 65001` 让 cmd.exe 以 UTF-8 解析批处理，而它按**字节位置**推进；含多字节中文/全角标点的行（如 `目录：%DISTDIR%`）会让位置漂移，
把后续命令切碎（实测 `：` 吃掉了 `%DISTDIR%` 的 `%`，输出成 `目录?DISTDIR`）。
**截断到前 59 行仍复现**，证明与文件长度无关，只与该行本身有关。

**修复（已落地）**：`打包Windows安装包.bat` 转 **GBK + CRLF**，`chcp 65001` → `chcp 936`。
中文 Windows 控制台原生就是 936，GBK 与它一致，无解析漂移。注意 `打包Windows桌面版.bat`、`setup.bat`、`dev_start.bat`
若也含 `chcp 65001` + 中文 echo，同样风险，改动时一并处理。

**连带坑：改编码后要同步测试的读取编码。** `tests/test_installer_packaging.py` 原先用
`read_text(encoding="utf-8")` 读这个 `.bat`，脚本转 GBK 后立刻 `UnicodeDecodeError`
→ 全量 2 failed（2026-10-12 才发现）。现在统一走 `_build_script_text()`（GBK 解码）。
教训：**编码是脚本与其契约测试之间共享的接口**，改一边必须改另一边；
`file <script>.bat` 报 `ISO-8859 text` 就是 GBK/ANSI 的信号，不是脚本坏了。

**判定**：脚本坏不坏，看**两个维度**——① 行尾是否 CRLF（根因一）；② 编码/代码页是否 UTF-8+65001（本条）。任一不合就坏。

## 根因二：含中文的 `.vbs` 必须是 ANSI/GBK（历史教训）

> 2026-10-11 已删除 `启动系统.vbs`，本条不再对应现存文件；保留是因为**将来若再加 `.vbs` 就会踩**。

`cscript` / `wscript` **按 ANSI 读取脚本**。UTF-8（含 BOM）的 `.vbs` 里出现中文文件名时解析失败，
报 `系统找不到指定的文件`，且**行号指向中文那行**。

受控实验（`.vbs` 里 `shell.Run "目标脚本.bat"`，目标是中文名文件）：

| `.vbs` 编码 | 结果 |
|---|---|
| UTF-8 | ✗ 失败 |
| UTF-8 + BOM | ✗ 失败 |
| **GBK(ANSI)** | ✓ 正常 |
| UTF-16LE + BOM | ✓ 正常 |

选 **GBK**（而非 UTF-16LE）是为了 git 可 diff —— UTF-16 会被当二进制。
`.gitattributes` 里保留 `*.vbs text eol=crlf` 就是为了这个未来场景。

## 根因三：别依赖 `core.autocrlf`

`.bat` 在 git 里存的永远是 LF（`core.autocrlf=true` 提交时把 CRLF normalize 成 LF）。
所以工作区是否拿到 CRLF，**完全取决于检出的机器/该 git 安装的 `core.autocrlf`**：

- `core.autocrlf=true` → 检出 CRLF → 能跑
- `core.autocrlf=false`（或换机器/换 git 发行版）→ 检出 LF-only → **坏**

**本机现状**：git 系统配置里 `core.autocrlf=true`，所以**全新 clone 本来就是 CRLF、能跑**；
本仓库工作区曾出现 LF-only，是历史遗留的陈旧检出。

## 判定

```bash
# 行尾：CR=0 且 LF>0 → LF-only（坏）
python -c "
import pathlib,sys
b=pathlib.Path(sys.argv[1]).read_bytes()
print('CR',b.count(b'\r'),'LF',b.count(b'\n'))" 打包Windows安装包.bat

# 属性是否生效
git check-attr text eol -- 打包Windows安装包.bat
```

**别用 `grep -c $'\r'` 判行尾**：grep 按行工作、CR 被吞掉，会误报 0。用 Python 数字节。

## 已落地的修复

1. **`.gitattributes`（durable fix）** —— 不依赖任何人的 git 配置：

   ```
   *.bat text eol=crlf
   *.cmd text eol=crlf
   *.vbs text eol=crlf
   ```

   验证方式（关键）：`git -c core.autocrlf=false clone` 一个临时副本，提交 `.gitattributes` 后
   `git checkout -- .`，确认 `git check-attr eol` 为 `crlf` 且检出行尾确为 CRLF。
   **不提交 `.gitattributes` 时该测试会失败**（检出仍是 LF）—— 证明它是真正生效的那一环。

2. ~~`启动系统.vbs` 编码 UTF-8 → GBK~~ —— 该文件已于 2026-10-11 删除；教训见「根因二」。

3. **工作区 `.bat` 行尾已转 CRLF** —— 但 git **看不到内容变化**（normalize 掉了），
   所以 `.bat` 的「修复」是工作区本地状态 + `.gitattributes` 保证今后检出正确。

## 执行脚本时的注意事项

**用 CRLF 副本执行**（工作区已是 CRLF 时可省略这步；副本法仍适用于确认有问题的文件）：

```bash
python -c "
import pathlib
src = pathlib.Path('打包Windows安装包.bat').read_bytes()
pathlib.Path('_build_tmp.bat').write_bytes(src.replace(b'\n', b'\r\n'))
"
cmd.exe /c "_build_tmp.bat < nul"
rm -f _build_tmp.bat
```

- **ASCII 文件名**：cmd 在本机代码页下把中文文件名看成乱码，传中文名会「找不到文件」，所以副本用 ASCII 名。
- **`< nul`**：脚本末尾有 `pause`，重定向 stdin 才不会被挂住。
- **`cmd.exe /c`（不是 `cmd //c`）**：MSYS 下 `//c` 会被当路径处理，只开个交互 cmd 不执行命令。

## 更稳的替代：拆成 Python

中文路径多的步骤（补 `config.json`、ISCC、ZIP）直接用项目 venv 的 Python 跑，
彻底绕开 cmd 代码页问题：

```python
subprocess.run([str(ISCC), "/Qp", f"/DAppVersion={VERSION}",
                f"/DSourceDir={APPDIR}", f"/DOutputDir={DIST}", str(ISS)],
               cwd=str(ROOT), capture_output=True, text=True,
               encoding="utf-8", errors="replace")
```

Python 传参用宽字符 API，中文路径不经 cmd 解析，最可靠。

## 别做

- 别用 `grep -c $'\r'` 判行尾（会误判为 0）。
- 别把 `.bat` 根因说成「UTF-8 编码问题」——UTF-8+CRLF 实测正常。
- 别只看 `chcp 65001` 就以为修好了。
- 别在中文路径上用 `cmd //c` + 中文文件名。
- 别只用「本机能跑」证明修好：本机 `core.autocrlf=true` 会掩盖问题，
  必须用 `core.autocrlf=false` 的 clone 验证 `.gitattributes` 真的生效。
- 别把含中文的 `.vbs` 存成 UTF-8（哪怕带 BOM）。
- 改完 `.bat` 行尾后别指望 git diff 能体现——它被 normalize 掉了；靠 `.gitattributes` 兜底。

## 遗留：本机 `.venv` 是 Linux 版，源码服务起不来（与行尾无关）

本机 `.venv` 只有 `bin/`、`lib64/`，**没有 `.venv\Scripts\python.exe`** —— 是 Linux venv。
所以 `.venv\Scripts\python.exe app\app.py` 找不到解释器。
而 `setup.bat` 只判断 `if exist .venv`，见目录存在就跳过创建，**卡在「目录在、环境不完整」**。
重建 Windows venv 属独立事项（`py -3 -m venv .venv`），或先删掉那个 Linux `.venv` 再跑 `setup.bat`。
