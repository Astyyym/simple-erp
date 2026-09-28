# 销售单纸张 241×140（二联连续纸）

> **历史方案，不是现行规格。** 2026-09-25 已确认销售/退货及汇总 PDF 改用 A4 竖版，销售/退货内容正向重排；不要再把本文件中的 241×140mm 尺寸、版心和驱动选项当作当前要求。现行规则见 [`print-offset-calibration.md`](./print-offset-calibration.md)。

User-measured 2026-07-14. Supersedes treating **280/288 as single-page height**.
Short plan: `开发短计划/2026-07-14_销售单241x140二联纸版面_执行计划.md`.

## Spec

| 量 | 值 | 备注 |
|----|-----|------|
| 一联宽 | **241mm**（尺量约 240） | 含两侧导孔；行业 9.5" 连续纸 |
| 一联高 | **140mm** | 撕开后打一单 |
| 两联未撕总高 | ≈ **280mm** | 非 PDF 页高 |
| 包装/旧文档 | 241×280 或 241×288 | 参考；**以一联尺子为准** |
| 左孔带到正文 | ≈ **20mm** | 左边距硬约束 |
| 右边距 | **20mm** | **与左等宽**；否则视觉偏右 |
| 版心宽 | **201mm** | 241 − 20 − 20 |
| 联式 | 二联 | 一单一页 PDF + 叠打 |
| 打法 | 一单一页 | 不是 PDF 画 280 高再撕 |

`printer_paper_width_mm=241`，`printer_paper_height_mm=140`。

## 包装 vs 尺子

用户曾：盒标 241×288、手量约 14×24cm（=140×240）、后确认二联连在一起≈280。

| 说法 | 含义 |
|------|------|
| 手量 24×14 cm | 一联宽×高 → **PDF 页** |
| 两联未撕 ≈280 | 140×2；旧代码错把这当单页 |
| 盒标 288/280 | 标称/连张，**不当** `@page` 高 |

**原则：以撕开一联尺子为准。** 问用户时要：宽（含孔）、高（撕口到撕口）、左孔到正文、是否二联/三联叠打。

## 为何预览对、实打偏

1. PDF/`@page` 若按 **280** 画，物理纸只有 **140** → 驱动缩放/裁切 → 靠下靠左。
2. 流式 padding + 忽略孔边 → 版心漂。
3. **不是**优先 offset 问题。用户明确：恢复 offset 0，按版面重做。

## 居中失败（用户连纠两次）

| 错误 | 正确 |
|------|------|
| 左 20 / 右 10 | 左右必须对称 20 |
| 仅 `padding: 6mm 20mm` 就说居中 | WeasyPrint 下表格仍可能挤出右缘；用户会贴图问「这居中吗？」 |
| 标题 `text-align:center` + `letter-spacing` | 字距会视觉右偏；公司名 `letter-spacing:0` |
| 名称规格 `<col>` 无宽度吞整行 | 列宽合计 **= 201mm**（约 12/78/18/24/32/37） |
| 只丢 `/settings/print-preview` URL | 大屏白底矮条像空白；给 **Windows PDF 路径** |

用户原话：量了左边孔距后，右边也应等宽；「这居中吗？」时先认错再量，勿辩。

### 当前落地（认居中前必过）

```css
.page { width:241mm; height:140mm; position:relative; overflow:hidden; }
.sheet {
  position:absolute; left:20mm; right:20mm; top:6mm; bottom:6mm;
  width:201mm; overflow:hidden;
}
table.items { width:201mm; max-width:201mm; table-layout:fixed; }
```

页脚 `footer-sign` 约 **38mm**。offset 默认 0。

### 页脚贴表，不贴页底（用户 2026-07-14）

140mm 矮页若把 `.bottom` 设成 `position:absolute; bottom:0`，电话/地址/签字会贴在纸最下，中间大片空白——用户明确不要。

正确：

```css
.bottom {
  /* 紧贴表格下方，不贴页面底边 */
  margin-top: 2.5mm;
  /* 禁止 absolute + bottom:0 */
}
```

文档流：标题 → 明细表 → 页脚紧跟。短内容时下半页留白可以。验收时量「表格底到订货电话」≈2–4mm，不是「订货电话贴页底」。

### 墨迹边距测量（验收门）

1. WeasyPrint 写 `temp_pdf/*.pdf`
2. `pypdfium2` 渲 PNG（scale≥2）
3. 灰度墨迹 bbox → 左右边距 mm
4. 目标：L≈R≈20、|L−R|<1mm、右缘不裁、小计完整

例（2026-07-14 修后）：L=19.87 R=20.10 差 0.24mm。页脚贴表后底部墨迹边距可远大于 6mm（空白正常）。

## 修法顺序

1. 尺子：一联宽×高、孔边到正文。
2. 改 `@page` / `.page` = 一联 mm。
3. **绝对对称 `.sheet` + 列宽合计=版心**。
4. 驱动：自定义 241×140；关适合页面。
5. offset 仅 1–3mm 机差；默认 0。
6. 交付：PDF 路径 + 可选 PNG；重启 5001。

## 给用户看什么

`/settings/print-preview` = 无侧栏白底矮条，大屏像空白（用户：「链接我看不出来」）。

优先：

1. `temp_pdf/销售单_…预览.pdf` → **Windows 路径** 双击
2. 登录设置页点「预览打印效果」
3. 真实订单 PDF

## 与 offset 文档

`references/print-offset-calibration.md` = **最后**微调。页尺寸错、左右不等宽、表格溢出时禁止大 offset 当主修法。

## 验收

- 预览 HTML：`size: 241mm 140mm` + `left:20mm`/`width:201mm`；无 `size: 241mm 280mm`
- 墨迹边距 L≈R≈20；小计列完整
- pytest：`tests/test_print_workflows.py`
- 实打一联：左右对称、孔边≈20mm
