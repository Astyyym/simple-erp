# 数据根排查：别把 `-wal`/`-shm` 的 mtime 当成「有人在写」

## 症状

看 `Documents\简单ERP数据\data\` 时发现：

- `erp.db-wal` / `erp.db-shm` 的 mtime 在持续变动，主库 `erp.db` 却不动；
- 怀疑「是不是有别的进程在跟正式库较劲」。

**先别下这个结论。** 这两个文件本来就容易被自己的排查动作刷新。

## 关键事实：只读连接也会创建 `-wal`/`-shm`

库处于 WAL 模式（本项目的默认）时，**即使 `mode=ro` 的只读连接**也会创建
`-wal`/`-shm`，关闭后文件仍留着，且每次新连接都刷新 `-shm` 的 mtime：

```python
# 复现（对正式库只读，无写入）
c = sqlite3.connect('file:.../erp.db?mode=ro', uri=True)
c.execute('SELECT COUNT(*) FROM orders').fetchone()
c.close()
# → 目录里出现 erp.db-shm / erp.db-wal；-shm mtime = 刚才
```

所以「mtime 一直在变」很可能只是**你自己刚跑过的只读查询**。排查时先问：
我上一次读这个库是什么时候？

## 怎么证明「正式库一个字节都没被写过」

三条硬证据，按强度排序：

| 证据 | 命令 | 判定 |
|---|---|---|
| **WAL 大小** | `stat -c %s .../erp.db-wal` | **0 字节** → 从未有任何事务提交。WAL 模式下有任何写入，这个文件必然 > 0 |
| **待提交帧数** | `PRAGMA wal_checkpoint(PASSIVE)` | 返回 `(0, 0, 0)` → 无待提交帧 |
| **结构指纹** | `PRAGMA table_info(...)` 查某个**只有新代码才会加的列** | 列不存在 → 新代码的 `init_db()` 没跑过它 |

第三条特别有用：比如某次改动会幂等加 `order_items.cost_source`，那么只要正式库里
**没有**这列，就能证明该次改动的迁移从未在正式库上执行过。

注意：主库 `erp.db` 的 mtime 会因**只读打开**而刷新，所以「mtime 变了」不等于「被写了」。
用内容/结构判定，不要用时间戳判定。

## 怎么找出「是谁碰了数据根」

日志（`<数据根>\logs\app.log`）只能告诉你**有过一次启动**，给不出调用方。配合这几步定位：

1. **日志语句指纹**——不同启动路径打不同的行：
   - `Serving on http://127.0.0.1:5000` → **waitress**，即桌面壳（`desktop_app.py`）或 EXE；
   - `Running on http://...`（前缀 `werkzeug`）→ Flask 自带服务器，即 `python app\app.py`。
2. **EXE 的 LastAccessTime**——绿色包 `dist\local-vX.Y.Z\简单ERP\简单ERP.exe` 被运行过，
   它的 `LastAccessTime` 就会接近那次日志时间（用 PowerShell `Get-Item ... | Select LastAccessTime`）。
3. **进程创建时间窗口**——`Get-CimInstance Win32_Process` 按 `CreationDate` 过滤该时间点前后。
4. **Hermes 侧排除**——`grep` `%LOCALAPPDATA%\hermes\logs\agent.log*`，确认没有会话
   （含自己）调用过该 EXE。

绿色版 EXE 旁边没有自己的 `data\`，其 `config.json` 的 `database_path` 是相对路径
`data/erp.db`，经 `project_path()` 解析到**记忆的数据根**——所以「运行一次绿色版」就会
在正式数据根留下一次启动日志 + 刷新主库 mtime，但**不产生任何写入**。

## 别做

- 别用 `-wal`/`-shm` 的 mtime 判断「有人在写」——只读连接就会刷。
- 别用主库 mtime 判断「被写了」——只读打开也会刷。
- 别在没看 WAL 大小前就向用户报「可能有别的进程在动数据」；先说清哪几条证据支持、哪几条还没取。
- 排查用的只读探针跑完，记得意识到它自己留下了 `-wal`/`-shm`（无害，但别当成新线索）。
