"""PyInstaller hook：pillow-heif（HEIC/HEIF 支持）。

为什么需要它：`pillow_heif` 的 HEIC 解码依赖 `_pillow_heif` 扩展与随包的 libheif
动态库/插件数据。不收集的话源码能跑，但打包出来的 EXE 上传/预览 HEIC 会直接失败
（且没有任何源码测试会红 —— 只在打包形态暴露）。

放置位置由两个 spec 的 `hookspath` 指向 `packaging/hooks/`，PyInstaller 分析到
`pillow_heif` 时自动套用本 hook。
"""
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

hiddenimports = collect_submodules("pillow_heif")
datas = collect_data_files("pillow_heif")
binaries = collect_dynamic_libs("pillow_heif")
