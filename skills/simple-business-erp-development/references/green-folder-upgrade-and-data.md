# Windows 桌面版数据目录与升级安全

## 当前目录模型

冻结桌面版将程序文件与业务数据分开：

```text
程序目录\
  简单ERP.exe
  _internal\
  config.json

数据根目录\
  data\erp.db
  backups\
  imports\
  logs\
  temp_pdf\
```

- 新装桌面版默认数据根：`%USERPROFILE%\Documents\简单ERP数据`。
- `%LOCALAPPDATA%\简单ERP\data_location.json` 只记录数据根路径。
- 程序根与业务数据根可不同；源码运行默认将数据放在项目目录，不能把源码默认路径误当 EXE 首装路径。
- 旧安装仍可能把 `data\` 放在 EXE 旁。升级前从设置页确认实际数据根，不要靠安装目录推断。

## 安全升级步骤

1. 从设置页确认当前数据根并退出程序。
2. 备份整个当前数据根，至少确认 `data\erp.db` 已复制。
3. 只更新程序文件（`简单ERP.exe`、`_internal\` 等）；不要用包内空库覆盖现有数据库。
4. 再启动程序，核对 `/health` 的版本和历史单据。
5. 数据迁移后旧目录仍保留；只有确认新库完整、路径正确且备份可用后，才考虑清理旧目录。

用**安装包**升级时不需要先卸载：直接运行新版本安装包覆盖程序文件即可，安装器不会覆盖 `config.json`、
也不会碰数据根；卸载同样只删程序文件。详见 [`windows-installer-inno-peruser.md`](./windows-installer-inno-peruser.md)。

绿色包则不需要通过卸载完成升级。整目录复制覆盖是主要数据风险，尤其旧数据仍在 EXE 旁时。

## 当前访问安全

当前版本永久免登录。桌面程序面向本机单机，服务默认绑定 `127.0.0.1:5000`；不要将服务开放给局域网/公网。`/health` 的 `auth: disabled` 仅报告产品免登录状态，不代表访问受控。

## 发布包与业务数据

构建和测试包不得携带真实 `erp.db`、业务日志、备份或导入文件。`dist/` 中的本地 ZIP 与 GitHub Release 是不同状态；发布前检查资产实际上传结果。详见 `versioning-github-backup.md`。
