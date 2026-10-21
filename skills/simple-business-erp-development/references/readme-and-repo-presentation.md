# README 与仓库门面维护

## 适用场景

改 `README.md`、调整 GitHub 仓库门面、补界面截图时。也用于回答「这个仓库第一眼看起来像什么」。

**硬性触发**：任何改变用户可见界面的改动（布局、配色、新增页面/区块、手机端外观），完成后都要重跑 `node tools/shots.mjs`。截图是最容易悄悄过期的东西 —— 过期截图比没有截图更误导人。

## 三条判断原则

1. **README 服务三类读者，按顺序**：路过的人（30 秒决定要不要看下去）→ 想装的人（找对产物）→ 改代码的人（找文档与命令）。段落顺序必须服从这个顺序。
2. **没有图的 README 等于不可信**。界面类项目第一屏之后就该有真实截图，且必须是真程序跑出来的，不是手绘示意图。
3. **版本历史不属于 README**。README 只回答「现在是什么」，逐版本交付内容属于 `CHANGELOG.md`。

## 已验证的结构模板

本次（2026-10-21，`v4.6.2`）落地并上线：

```text
居中标题区（项目名 + 一句话定位 + 徽章）
界面预览        ← 2×N 表格排布真实截图，每张带一句功能说明
这是什么        ← 3~4 条给人看的定位，不是功能清单
核心特性        ← 表格，一行一个能力域
快速开始        ← 三种分发形态对比表 + 「认名字」提示
功能说明        ← 按业务域分小节，一条一行
打印与纸型      ← 表格（纸型是不能出错的事实）
手机端          ← 单独成节 + 截图 + 三步上手 + 排障顺序 + 演进路线
安全边界
数据保护
开发            ← 环境要求、三行命令、质量基线、目录结构
文档导航        ← 表格指向 docs/
版本            ← 一句指向 VERSION + CHANGELOG + Releases
```

关键点：

- 「界面预览」放在最前面，因为它是路人唯一会看的部分。
- 「快速开始」必须是**对比表**而不是三段散文 —— 三种产物（安装包 / 绿色包 / APK）差异只能靠表格说清。
- 「版本」压到最后，且只有一句 + 两个链接。

## 版本流水账的正确去处

改之前先核对：README 里提到的每个版本号，`CHANGELOG.md` 是否都有独立章节。

```bash
for v in v4.6.1 v4.6.0 v4.5.0; do echo "$v -> $(grep -c "^## $v " CHANGELOG.md)"; done
```

全部命中才可以把段落删掉，只留一句指向 `CHANGELOG` + `Releases`。**核对不过就不要删** —— 那说明 CHANGELOG 有洞，先补 CHANGELOG。

## 截图怎么拍

### 首选：跑脚本（2026-10-21 起）

`tools/shots.mjs` 已把这些步骤全部自动化，**优先用它，不要手搓**：

```bash
node tools/shots.mjs                      # 全套重拍，约 55 秒
node tools/shots.mjs --only entry,orders  # 只重拍改动过的页
node tools/shots.mjs --skip-mobile        # 只拍桌面页
```

脚本自己完成：生成演示数据（`dist/` 被 gitignore，克隆后没有现成的）→ 起隔离实例（端口 5199）→ 逐页独立 CDP 截图 → 手机端真实配对 → 2 倍采样缩回 1440/390 宽 → 打印每张体积。

两条硬护栏，别绕过：

1. **数据根核对**：`waitHealth()` 会读 `/health` 的 `data_root`，不含 `/.alma/shots/demo_data` 就直接抛「拒绝截图」。这是刻意的 —— 截图是最容易被顺手对着真实账本做的事。
2. **手机端必须真配**：脚本在隔离库里现生成 6 位码、填 `#pairCode` 提交、确认停在 `/m/` 才截图。若报「配对后没停在 /m/」，多半是码过期，重跑一次即可。

改脚本里的页面清单就看 `DESKTOP_SHOTS` 数组；加页面只要追加 `['名字', '/路径/']`（**路径尾斜杠不能省**，否则 Flask 308 跳转、截到空白）。

### 手工兜底（脚本坏掉时）

1. 起隔离实例，绝不用真实账本：

```bash
ERP_PORT=5099 ERP_DATA_ROOT="<仓库>/.alma/shots/demo_data"   .venv/Scripts/python.exe app/app.py
curl -s http://127.0.0.1:5099/health    # 确认 data_root 是隔离目录
```

2. 桌面页用 CDP 逐页截，**每页独立进程 + 独立 `--user-data-dir`**（共用会出拼贴图）：

```javascript
await send('Emulation.setDeviceMetricsOverride', {width:1440,height:900,deviceScaleFactor:2,mobile:false});
await send('Page.navigate',{url:BASE+path});
await sleep(4000);                                  // 等 JS 渲染完，2.6s 不够
const r = await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
```

3. 手机端 `/m/` 未配对会 302 到 `/m/pair`：主应用里 `mobile_pairing.generate_pair_code()` 取码，起第二监听，CDP 填 `#pairCode` 后 `form.submit()`，确认跳到 `/m/` 再截。

### 规格与体积

- 桌面 1440×900、手机 390×844，都用 `deviceScaleFactor: 2` 采样
- **必须缩回**：2 倍原图 9 张能到 2.7 MB，缩到 1440 宽后约 1 MB
- JPG `quality=86`（手机 88）；落盘直接截 JPG，省掉转格式
- 存 `docs/images/shot-<页面>.jpg`，README 用 `<table>` 两列 + `<sub>` 写说明


## 四个必踩的坑

| 坑 | 现象 | 解法 |
|---|---|---|
| Git Bash 路径转换 | `node x.mjs /orders/new` 报 `Cannot navigate to invalid URL` | `MSYS_NO_PATHCONV=1 node ...` |
| heredoc 截断 | 一次性 `cat > README.md <<EOF` 写 16KB，文件只写到一半，`<<EOF` 标记都没闭合 | 分段写 `.alma/p1.md p2.md p3.md` 再 `cat` 合并；或直接用 Python `io.open(...).write()` |
| headless 截图拼贴 | 一张图里出现 2×2 个半页 | 每页独立进程 + 独立 `user-data-dir` |
| 前台命令被 3s 掐断 | `node ...` 秒退但退出码 1 | 长任务一律 `run_in_background: true` + `BashOutput` 等 |
| heredoc 吃掉转义 | `join('
')` / `split('
')` / `console.log('
...')` 里的 `
` 变成真实换行，`node --check` 报 `Invalid or unexpected token` | **别用 heredoc 写含转义的 JS**：改用 Edit 工具或 Python 脚本精确替换；写完必须 `node --check` |
| 2 倍图没缩回 | 截图 2880px 宽、单张 300KB、9 张 2.7MB | 采样 2x 后统一 `downscale()` 缩回 1440/390 |

## 顺手要修的失实描述

改 README 时逐条对源码核一次，别照抄原文：

- 「设置页提供网页效果与 PDF 样张」→ 实际只有 3 个「生成预览 PDF」按钮，HTML 样张路由 2026-10-18 已删
- 手机端配对限制 → 查 `erp/services/mobile_pairing.py`：「连续错 5 次锁 5 分钟」
- 分页条数、单联行数、纸型尺寸 → 分别查 `routes/orders.py`、`config.py` 的 `SLIP_SIZE_CHOICES`、打印 reference

## 验收清单

- [ ] 所有本地图片路径存在（脚本校验 `![...](...)` 与 `<img src="...">`）
- [ ] 所有 `docs/` 链接目标存在
- [ ] 图片未被 `.gitignore` 排除（`git check-ignore -v <path>`）
- [ ] 版本号与 `VERSION` 一致
- [ ] 截图来自演示数据，图注已声明这一点
- [ ] 没有把 `开发短计划/` 的私有路径写成可点击链接
- [ ] **UI 改版后重拍过 `docs/images/`**：跑了 `node tools/shots.mjs`，并逐张看过图（不是只看命令退出码）
