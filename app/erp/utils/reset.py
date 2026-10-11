"""清空所有数据（设置页「清空所有数据」）。

用户口径（2026-10-11）：「备份包和旧账留底不清，其他的全清」。

| 处理 | 对象 |
|---|---|
| 清空 | 四类单据与明细、客户/商品资料与客户价、库存状态/流水/期初、收付款与调整、对账快照、审计日志、单号序列表、`product_images/`、`temp_pdf/`、`logs/` |
| 保留 | `backups/`（安全网）、`legacy_archive/`（旧账留底）、数据目录路径记忆、`data/erp.db` 文件本身（清表不删库） |
| 回出厂 | 公司名/店名、打印信息、界面主题/缩放/字重、打印偏移与缩放 |

**单号流水高位保留**：清空前把各业务日期的 `MD`/`NH` 当日最大流水写回序列表
（`order_number_sequences` / `purchase_number_sequences`），清空后同一天的新单从高位续排，
避免与已打出的纸质单撞号。这是本模块唯一超出「删表」范围的设计。

安全设计：调用方必须先 `create_backup("pre_purge")` 并校验确认词；本模块在**单个事务**内完成
数据库清空，任一步失败整体回滚。
"""

from __future__ import annotations

import re
import logging
import shutil
from pathlib import Path

from erp import config as config_module
from erp.db import get_db
from erp.utils import order_numbering

# 删除顺序：先子表/引用方，再被引用方，满足外键约束。
CLEARED_TABLES = (
    "order_items",
    "orders",
    "purchase_return_items",
    "purchase_order_items",
    "purchase_return_orders",
    "purchase_orders",
    "customer_prices",
    "inventory_postings",
    "inventory_initializations",
    "product_inventory_state",
    "products",
    "payments",
    "adjustments",
    "reconciliation_snapshots",
    "customers",
    "audit_logs",
)

# 序列表单独处理：清空后要把保留的流水高位写回，不能简单 DELETE。
SEQUENCE_TABLES = (
    "order_number_sequences",
    "purchase_number_sequences",
    "purchase_return_number_sequences",
)

# 清空的运行目录（数据根下）。backups/ 与 legacy_archive/ 明确保留。
CLEARED_DIRNAMES = ("product_images", "temp_pdf", "logs")

_MD_ORDER_NO = re.compile(r"^MD(\d{8})(\d{4})$")


class PurgeError(ValueError):
    """Raised when the purge cannot be completed safely."""


def _counts(conn) -> dict[str, int]:
    counts = {}
    for table in CLEARED_TABLES + SEQUENCE_TABLES:
        try:
            counts[table] = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        except Exception:  # noqa: BLE001 — 老库可能缺表，计数缺失不影响清空
            counts[table] = 0
    return counts


def _table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _md_high_water(conn) -> dict[str, int]:
    """每个业务日期已用掉的 MD 最大流水（日期键统一为 YYYY-MM-DD）。"""
    if not _table_exists(conn, "orders"):
        return {}
    best: dict[str, int] = {}
    for row in conn.execute("SELECT order_no FROM orders WHERE order_no LIKE 'MD%'").fetchall():
        match = _MD_ORDER_NO.match(row["order_no"] or "")
        if not match:
            continue
        day, sequence = match.group(1), int(match.group(2))
        key = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        best[key] = max(best.get(key, 0), sequence)
    return best


def _nh_high_water(conn) -> dict[str, int]:
    """每个业务日期已用掉的 NH 最大流水（含历史 TN 退拿货号，口径与取号实现一致）。"""
    if not _table_exists(conn, "purchase_orders") or not _table_exists(conn, "purchase_return_orders"):
        return {}
    dates = {
        row[0]
        for row in conn.execute(
            "SELECT business_date FROM purchase_orders "
            "UNION SELECT business_date FROM purchase_return_orders"
        ).fetchall()
        if row[0]
    }
    # 复用取号模块的口径，避免这里再写一套 NH/TN 前缀规则造成漂移。
    return {date: order_numbering._highest_sequence(conn, date) for date in dates}


def _clear_table(conn, table: str) -> None:
    if not _table_exists(conn, table):
        return
    conn.execute(f'DELETE FROM "{table}"')
    # 让 AUTOINCREMENT 也回出厂，新数据从 1 开始编号。
    conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))


def _restore_high_water(conn, md: dict[str, int], nh: dict[str, int]) -> None:
    for business_date, last_sequence in md.items():
        if last_sequence <= 0:
            continue
        conn.execute(
            "INSERT INTO order_number_sequences(business_date,last_sequence) VALUES (?,?) "
            "ON CONFLICT(business_date) DO UPDATE SET last_sequence=excluded.last_sequence",
            (business_date, last_sequence),
        )
    for business_date, last_sequence in nh.items():
        if last_sequence <= 0:
            continue
        conn.execute(
            "INSERT INTO purchase_number_sequences(business_date,last_sequence) VALUES (?,?) "
            "ON CONFLICT(business_date) DO UPDATE SET last_sequence=excluded.last_sequence",
            (business_date, last_sequence),
        )


def _reset_open_log_files() -> set[str]:
    """把正在被日志句柄打开的日志文件截断到 0，并返回已处理文件的绝对路径集合。

    只删/截断文件是不够的：`RotatingFileHandler` 持有打开的文件对象，外部截断后它仍从旧偏移
    继续写，文件头部会留下 NUL 空洞。必须在句柄上 `truncate(0)` + `seek(0)`。
    """
    handled: set[str] = set()
    root = logging.getLogger()
    for handler in list(root.handlers):
        filename = getattr(handler, "baseFilename", None)
        if not filename:
            continue
        stream = getattr(handler, "stream", None)
        if stream is None:
            continue
        try:
            handler.flush()
            stream.truncate(0)
            stream.seek(0)
            handled.add(str(Path(filename).resolve()))
        except (OSError, ValueError):
            # 句柄不可用时退回普通文件处理（_purge_directory 会尝试删除或截断）。
            continue
    return handled


def _purge_directory(directory: Path, *, skip: set[str] | None = None) -> list[str]:
    """清空目录内容（保留目录本身）。Windows 上被占用的文件改为截断，不静默失败。"""
    warnings: list[str] = []
    skip = skip or set()
    if not directory.exists():
        return warnings
    for item in sorted(directory.iterdir()):
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
            continue
        if str(item.resolve()) in skip:
            # 已通过日志句柄正确截断，不再重复处理、也不再报「被占用」告警。
            continue
        try:
            item.unlink()
        except PermissionError:
            # 运行中的日志句柄在 Windows 上禁止删除/改名，但允许截断。
            try:
                with item.open("w", encoding="utf-8"):
                    pass
                warnings.append(f"{item.name} 正在被程序占用，已清空内容（重启后自动重建）")
            except OSError as exc:
                warnings.append(f"{item.name} 无法清理：{exc}")
        except OSError as exc:
            warnings.append(f"{item.name} 无法清理：{exc}")
    return warnings


def _reset_config_to_factory() -> None:
    """把可写配置整体回出厂默认（不动数据目录路径记忆——它在 LocalAppData）。"""
    config_module.save_config(dict(config_module.CONFIG_DEFAULTS), base={})


def purge_all_data() -> dict:
    """清空业务数据并回出厂设置。调用方负责自动备份与确认词。

    返回清空前的行数统计、保留的流水高位与目录清理告警，供界面留痕展示。
    """
    with get_db() as conn:
        counts = _counts(conn)
        md_high_water = _md_high_water(conn)
        nh_high_water = _nh_high_water(conn)

        for table in CLEARED_TABLES:
            _clear_table(conn, table)
        for table in SEQUENCE_TABLES:
            _clear_table(conn, table)
        _restore_high_water(conn, md_high_water, nh_high_water)

    warnings: list[str] = []
    # 先把正在被日志句柄打开的文件正确截断（否则句柄会从旧偏移续写，留下 NUL 空洞）。
    handled_logs = _reset_open_log_files()
    for dirname in CLEARED_DIRNAMES:
        skip = handled_logs if dirname == "logs" else None
        warnings.extend(_purge_directory(config_module.project_path(dirname), skip=skip))

    _reset_config_to_factory()

    return {
        "cleared_counts": counts,
        "md_high_water": md_high_water,
        "nh_high_water": nh_high_water,
        "directory_warnings": warnings,
    }


def purge_log_path() -> Path:
    """留痕文件：`logs/` 会被清空，因此留痕写在清空之后。"""
    return config_module.project_path("logs", "purge.log")


def write_purge_trace(result: dict) -> Path:
    """把本次清空写进 `logs/purge.log`（审计表已被清空，这里留一份可追溯的文本记录）。"""
    from datetime import datetime

    path = purge_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cleared_total = sum(result.get("cleared_counts", {}).values())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"{datetime.now().isoformat(timespec='seconds')} purge_all_data "
            f"cleared_rows={cleared_total} "
            f"md_dates={len(result.get('md_high_water', {}))} "
            f"nh_dates={len(result.get('nh_high_water', {}))}\n"
        )
    return path
