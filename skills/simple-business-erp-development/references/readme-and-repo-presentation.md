# README 与仓库门面维护

## 适用场景

改 `README.md`、调整 GitHub 仓库门面、补界面截图时。也用于回答「这个仓库第一眼看起来像什么」。

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

## 截图怎么拍（实测可用）

### 1. 起隔离实例，用绿色版自带的演示数据

绝不拿真实账本截图，也绝不污染正式 `data/erp.db`。

```bash
ERP_PORT=5099 ERP_DATA_ROOT="<仓库>/dist/local-v<版本>/简单ERP/demo_data"   .venv/Scripts/python.exe app/app.py
curl -s http://127.0.0.1:5099/health    # 确认 data_root 指向 demo_data
```

### 2. 桌面页用 CDP 逐页截（每次独立进程）

`--headless=new --screenshot` 连续跑多个 URL 会产出**拼贴图**（多页叠在一张里）。必须每页独立 CDP 会话 + 独立 `--user-data-dir`：

```javascript
await send('Emulation.setDeviceMetricsOverride', {width:1440,height:900,deviceScaleFactor:2,mobile:false});
await send('Page.navigate',{url:BASE+path});
await sleep(4000);                                  // 等 JS 渲染完，2.6s 不够
const r = await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
```

### 3. 手机端必须先真实配对

`/m/` 未配对会 302 到 `/m/pair`，直接截会截到配对页。真实流程：

1. 主应用里生成配对码：`from erp.services import mobile_pairing` → `mobile_pairing.generate_pair_code()`
2. 起第二监听（`erp.mobile_app.create_mobile_app()`，端口 5098 避开 5001 占用检测）
3. CDP 打开 `/m/pair`，填 `#pairCode` 隐藏域后 `form.submit()`
4. 等待跳转到 `/m/` 再截图

### 4. 规格与体积

- 桌面：1440×900，`deviceScaleFactor: 2`（2 倍图），落盘后缩到 1440 宽
- 手机：390×844，同样 2 倍
- 统一转 JPG `quality=86, optimize=True`；9 张桌面 + 1 张手机 ≈ 1.1 MB，仓库不会臃肿
- 存 `docs/images/shot-<页面>.jpg`，README 用 `<table>` 2 列排布 + `<sub>` 写说明

## 四个必踩的坑

| 坑 | 现象 | 解法 |
|---|---|---|
| Git Bash 路径转换 | `node x.mjs /orders/new` 报 `Cannot navigate to invalid URL` | `MSYS_NO_PATHCONV=1 node ...` |
| heredoc 截断 | 一次性 `cat > README.md <<EOF` 写 16KB，文件只写到一半，`<<EOF` 标记都没闭合 | 分段写 `.alma/p1.md p2.md p3.md` 再 `cat` 合并；或直接用 Python `io.open(...).write()` |
| headless 截图拼贴 | 一张图里出现 2×2 个半页 | 每页独立进程 + 独立 `user-data-dir` |
| 前台命令被 3s 掐断 | `node ...` 秒退但退出码 1 | 长任务一律 `run_in_background: true` + `BashOutput` 等 |

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
