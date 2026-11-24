"""手机只读端的配对门：6 位配对码 + 设备 token + Set-Cookie 会话。

设计约束（见 `docs/需求/2026-10-17_安卓只读端架构决策.md` §4）：

- **唯一的写端**就是这里，且只写 `data/mobile_devices.json`，**绝不写 `erp.db`**。
  除配对提交本身，手机端所有查询都走只读连接（`services/readonly_queries.py`）。
- 浏览器方案下无法给每个页面请求加 header，所以会话用 **Set-Cookie**。
- cookie 里带设备 id + 明文 token；文件里只存 token 的 sha256，比较走
  `hmac.compare_digest`。手机丢失 → 桌面端撤销设备 → 立刻失联（阶段一不落本地数据）。
- 6 位码：每分钟最多生成 3 次、有效期 120 秒、连续错 5 次锁 5 分钟。

**设备身份 uid（2026-10-24 新增，store v2）**

同一台手机重复配对会长出两条记录（用户实测报过）：手机点「改地址」会清 cookie
再配一次，服务端原来每次都 `append`。现在客户端上报一个稳定的 `device_uid`，
服务端**同 uid 原地更新、不新增记录**。

uid 的信任边界必须写清：它是**客户端可伪造**的，只用于「识别是不是同一台」，
**不参与认证**。认证仍然只有 token（sha256 + `compare_digest`）。伪造 uid 得先拿到
6 位配对码，而拿到码的人本来就能配对，所以它不扩大攻击面。

时间一律存本地时间 ISO 串（与项目其它 JSON 配置一致），便于设置页直接展示。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from erp.config import project_path

logger = logging.getLogger(__name__)

COOKIE_NAME = "erp_mobile"
# 浏览器端「这台设备是谁」的 cookie（**不是**认证凭据，只是 uid 的载体）。
# 配对页 GET 时服务端下发，POST 时服务端自己从 cookie 读 —— 不需要任何前端 JS，
# 所以保持 httponly。泄露它没有后果（见上面 uid 的信任边界）。
UID_COOKIE_NAME = "erp_device_uid"

PAIR_CODE_LENGTH = 6
PAIR_CODE_TTL_SECONDS = 120
CODE_GENERATIONS_PER_MINUTE = 3
MAX_FAILED_ATTEMPTS = 5
LOCK_SECONDS = 300

# v1 → v2：设备记录新增 `uid` / `last_sync_at`；载入时按 uid 去重。
STORE_VERSION = 2

# uid 只接受 URL 安全字符，长度 6–64。原生端发 16 hex，浏览器端发 32 hex。
_UID_RE = re.compile(r"^[0-9A-Za-z_-]{6,64}$")


# ---------------------------------------------------------------------------
# 设备登记文件的并发门
# ---------------------------------------------------------------------------
# 为什么必须有它：两个监听跑在**同一个进程**里（`desktop_app.py` 主服务 threads=8、
# `mobile_server.py` 第二监听 threads=4），而下面 6 个写者都是
# 「load_store() → 就地改 → save_store()」：生成码、校验码、记访问、记同步、
# 撤销单台、撤销全部。没有锁时踩过的三种真事故：
#   ① 丢更新 —— 两台手机同时配对，最后文件里只剩一台（记录凭空消失），
#      或者两台手机撤销后又被并发的 mark_synced 写回来；
#   ② `os.replace` 在 Windows 上撞 `PermissionError`（目标正被另一个线程的读句柄
#      打开）→ `mark_synced` 抛异常 → 快照请求 500，手机端表现为「同步失败」；
#   ③ 固定临时名 `.json.tmp` 被两个写者共用，一个还没 replace 就被另一个截断，
#      写进去的内容不是你那一份。
#
# ⚠️ **读也进锁**（只锁写等于没锁）：`load_store()` 拿到的快照一旦离开锁，
# 拿它的那个写者其实已经在写一份过期数据了 —— 事务边界必须是「读到写」整段。
# 用 `RLock` 是因为写者会在事务里再调 `load_store` / `save_store`，同线程必须能重入。
_STORE_LOCK = threading.RLock()

# `os.replace` 的瞬时失败退避（秒）。Windows 上「目标被别的句柄打开」是常态，
# 退避 20/50/100ms 三次基本能过去；再长就会按住 HTTP 线程，而这个调用点在
# `/m/api/snapshot` 的同步路径上（手机在等）。
_SAVE_RETRIES = (0.02, 0.05, 0.1)

# 上一份成功写入的副本。`load_store` 遇到坏文件时用它恢复（见 `_recover_from_corrupt`）。
_BACKUP_SUFFIX = ".bak"


def store_path() -> Path:
    """设备登记文件路径：跟数据根走（`project_path` = `data_root`），测试用临时根。"""
    return project_path("data", "mobile_devices.json")


def _now() -> datetime:
    return datetime.now()


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _empty_store() -> dict:
    return {
        "version": STORE_VERSION,
        "devices": [],
        "code": None,
        "code_generations": [],
        "failures": {"count": 0, "locked_until": ""},
        "last_success_at": "",
    }


def load_store() -> dict:
    """读设备登记（自己拿 `_STORE_LOCK`，写者才可能在锁内组成完整事务）。

    「文件不存在 = 空 store」是**正常语义**：首次运行、刚换数据根、测试的临时根
    都会走到这里，不能告警也不能顺手造一个空文件。

    真正的故障（读失败 / 解析失败）**不能静默返回空表**：任一写者拿到空表后都会
    `save_store` 把它写回，坏文件当场被固化，事后没有任何证据能解释「用户为什么
    所有手机同时掉配对」。所以坏文件一律留档 + 告警 + 优先从 `.bak` 恢复。
    """
    with _STORE_LOCK:
        path = store_path()
        if not path.exists():
            return _empty_store()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            payload = _recover_from_corrupt(path, exc)
        return _normalize_store(payload)


def _recover_from_corrupt(path: Path, exc: Exception) -> dict:
    """坏文件的处理：留档 → 告警 → 尽量从 `.bak` 恢复；都失败才给空表。

    留档名带时间戳、**不覆盖**旧档：同一天坏两次时，要能拿到最早那一次的现场
    （那才是正常内容，后一次可能已经是空表被写坏的结果）。
    """
    logger.warning("设备登记文件读取/解析失败，按损坏处理：%s（%s）", path, exc)
    corrupt = path.with_name(f"{path.name}.corrupt-{_now().strftime('%Y%m%d-%H%M%S')}")
    try:
        path.replace(corrupt)
        logger.warning("损坏文件已留档：%s", corrupt)
    except OSError:
        # 留档失败不能挡路（文件可能被别的句柄锁着）：照旧往下走，让写者用新文件覆盖。
        logger.warning("损坏文件留档失败，继续按备份/空表处理：%s", path, exc_info=True)

    backup = path.with_name(path.name + _BACKUP_SUFFIX)
    if backup.exists():
        try:
            recovered = json.loads(backup.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            recovered = None
        if isinstance(recovered, dict):
            logger.warning("已从 %s 恢复设备登记（上一次成功保存的副本）。", backup)
            # 立刻写回：只在内存里「恢复」是不够的 —— 下一次 `load_store` 看到文件
            # 不存在又会是空表（损坏文件已被改名），读到哪一份全看请求顺序。
            # 写回失败不再往上抛：这已经是「坏文件 + 写不进去」的双重故障，
            # 把异常扔给 HTTP 请求只会让设置页 500，反而看不到这条诊断线索。
            try:
                save_store(recovered)
            except OSError:
                logger.warning("恢复出的设备登记写回失败（继续用内存里的内容）：%s", path, exc_info=True)
            return recovered
    return _empty_store()


def _normalize_store(payload) -> dict:
    """把磁盘内容收敛成当前版本的形状（缺字段补默认、非法字段重置、按 uid 去重）。"""
    store = _empty_store()
    if isinstance(payload, dict):
        store.update({key: payload.get(key, store[key]) for key in store})
    if not isinstance(store.get("devices"), list):
        store["devices"] = []
    if not isinstance(store.get("code_generations"), list):
        store["code_generations"] = []
    if not isinstance(store.get("failures"), dict):
        store["failures"] = {"count": 0, "locked_until": ""}
    store["devices"] = _dedupe_by_uid(store["devices"])
    store["version"] = STORE_VERSION
    return store


def _dedupe_by_uid(devices: list) -> list:
    """同 uid 多条只留 `paired_at` 最新的那条；无 uid 的旧记录原样保留（顺序不变）。

    为什么在**读取时**去重、而不是只在写入时判断：v1 的库已经因为旧逻辑长出过
    重复记录（用户实测两条「手机 App」），升级后第一次打开设置页就应该只看到一条，
    而不是等用户再配一次才收敛。这里是纯函数式的，`load_store()` 每次都会走。
    """
    kept: list = []
    best_index: dict[str, int] = {}
    for device in devices:
        if not isinstance(device, dict):
            continue
        uid = normalize_device_uid(device.get("uid", ""))
        if not uid:
            kept.append(device)
            continue
        existing = best_index.get(uid)
        if existing is None:
            best_index[uid] = len(kept)
            kept.append(device)
            continue
        # 同 uid：留配对时间更晚的一条；`last_seen_at` / `last_sync_at` 取两者较大值，
        # 免得把「最后一次真的连上过」的时间一起丢掉。
        previous = kept[existing]
        if str(device.get("paired_at", "")) > str(previous.get("paired_at", "")):
            merged = dict(device)
            kept[existing] = merged
        else:
            merged = dict(previous)
        merged["last_seen_at"] = max(
            str(previous.get("last_seen_at", "") or ""), str(device.get("last_seen_at", "") or "")
        )
        merged["last_sync_at"] = max(
            str(previous.get("last_sync_at", "") or ""), str(device.get("last_sync_at", "") or "")
        )
        kept[existing] = merged
    return kept


def normalize_device_uid(value) -> str:
    """收敛客户端上报的设备 uid；非法一律当空串（**不信任何花哨输入**）。

    空串的含义是「这台客户端没有稳定身份」，此时退回旧行为（每次配对新记录）——
    旧版 App / 第三方客户端仍然能配对，只是不能自动合并。
    """
    text = str(value or "").strip()
    return text if _UID_RE.match(text) else ""


def new_device_uid() -> str:
    """服务端为浏览器端生成一个 uid（32 hex）。"""
    return secrets.token_hex(16)


def save_store(store: dict) -> None:
    """原子写：唯一临时文件 + `fsync` + `replace`，并留一份 `.bak`。

    调用方通常在 `_STORE_LOCK` 事务里；本函数自己也拿锁（RLock 可重入），
    这样测试或脚本直接调它也安全。

    三个细节都是踩出来的：
    - **临时名带 pid + 线程 id**：固定 `.json.tmp` 时两个线程共用同一个临时文件，
      一个还在写、另一个已经 replace，结果是 `FileNotFoundError` 或者文件内容串台。
    - **`flush` + `os.fsync`**：不 fsync 就 replace，断电后可能出现「正式文件是 0 字节」
      —— 那比半截 JSON 更糟（连留档都读不出东西）。
    - **退避重试**：Windows 上 `os.replace` 会在目标被其它句柄打开时抛
      `PermissionError`（杀软、备份进程、别的线程的读句柄都会），这个失败是瞬时的。
    """
    with _STORE_LOCK:
        path = store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(store, ensure_ascii=False, indent=2) + "\n"
        temp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        last_error: OSError | None = None
        for attempt, delay in enumerate((0.0, *_SAVE_RETRIES)):
            if delay:
                time.sleep(delay)
            try:
                _write_text_durably(temp, text)
                _refresh_backup(path)
                temp.replace(path)
                return
            except (PermissionError, FileNotFoundError) as exc:
                last_error = exc
                logger.warning("设备登记保存失败（第 %d 次，稍后重试）：%s", attempt + 1, exc)
        try:
            temp.unlink()
        except OSError:
            pass
        # 循环至少执行一次，走到这里必然有 last_error（不用 assert：`-O` 会把断言抹掉，
        # 那就变成 `raise None` 的 TypeError，比原始故障更难查）。
        if last_error is None:  # pragma: no cover - 只有并发改代码才会到
            raise OSError(f"设备登记保存失败（未知原因）：{path}")
        raise last_error


def _write_text_durably(temp: Path, text: str) -> None:
    """写临时文件并落盘（`fsync`），否则 `replace` 之后断电可能留下空文件。"""
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _refresh_backup(path: Path) -> None:
    """把当前（即将被覆盖的）文件复制成 `.bak`，供坏文件恢复用。

    失败一律忽略：备份是「有更好」的保险，绝不能因为它挡住正式写入
    （目标文件被别的句柄打开时，Windows 连复制都会拒绝）。
    """
    if not path.exists():
        return
    try:
        shutil.copyfile(path, path.with_name(path.name + _BACKUP_SUFFIX))
    except OSError:
        logger.warning("设备登记备份失败（已忽略）：%s", path, exc_info=True)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lock_remaining(store: dict) -> int:
    locked_until = _parse(store["failures"].get("locked_until", ""))
    if locked_until is None:
        return 0
    remaining = int((locked_until - _now()).total_seconds())
    return max(remaining, 0)


def lock_remaining_seconds() -> int:
    return _lock_remaining(load_store())


# ---------------------------------------------------------------------------
# 明文码的进程内短缓存（只为「设置页能反复显示同一个码」）
# ---------------------------------------------------------------------------
# 为什么需要它：磁盘上只存 sha256（这是对的，别改）。但设置页要能：
#   ① 刷新页面后仍看到同一个码（否则用户一刷新就「码没了」，还得再点一次）；
#   ② 倒计时每秒走一格、走完变成「已过期」；
#   ③ 点「换一个」当场换码，不整页重渲染。
# 这三件事都要求**明文**在有效期内可再次取出。所以只放在内存里，不落盘、
# 不进 session cookie（session cookie 客户端可读），进程退出即消失。
#
# ⚠️ 缓存必须**跟磁盘上的那条码对得上**才允许返回。这是踩出来的：
# 测试里每个用例换一个数据根（`mobile_devices.json` 是新的、没有码），
# 而模块级缓存还留着上一个用例的明文 —— 于是「没生成过码」的用例读到了别人的码。
# 生产上同样成立：换数据根、备份恢复、手工删 `mobile_devices.json` 之后，
# 缓存里的旧码必须立刻失效，否则设置页会显示一个**服务端根本不认**的 6 位数，
# 用户输进去只会得到「配对码已过期」，而屏幕上明明写着还剩 90 秒。
_plaintext: dict = {"code": "", "expires_at": None, "created_at": ""}


def _remember_plaintext(code: str, expires_at: datetime | None, created_at: str = "") -> None:
    _plaintext["code"] = str(code or "")
    _plaintext["expires_at"] = expires_at
    _plaintext["created_at"] = str(created_at or "")


def current_plaintext_code() -> tuple[str, int]:
    """当前仍有效的明文配对码与剩余秒数；没有/已过期/与磁盘对不上返回 `("", 0)`。"""
    code = str(_plaintext.get("code") or "")
    expires = _plaintext.get("expires_at")
    created_at = str(_plaintext.get("created_at") or "")
    if not code or not isinstance(expires, datetime):
        return "", 0

    # 与磁盘上的那条码核对：hash 与 created_at 都得对上，否则缓存作废。
    stored = load_store().get("code") or {}
    if (
        not stored.get("hash")
        or str(stored.get("created_at", "")) != created_at
        or not hmac.compare_digest(_sha256(code), str(stored.get("hash")))
    ):
        _remember_plaintext("", None)
        return "", 0

    left = int((expires - _now()).total_seconds())
    if left <= 0:
        _remember_plaintext("", None)
        return "", 0
    return code, left


def generate_pair_code() -> tuple[str, str]:
    """生成一次 6 位配对码，返回 `(code, error)`；被限速或锁定时 code 为空串。

    整个「读 → 限速判定 → 写」必须在 `_STORE_LOCK` 内：否则两个并发的生成请求
    会各自读到「还没生成过」，一起通过限速判定 —— 一分钟三次的上限直接失效。
    """
    with _STORE_LOCK:
        store = load_store()
        if _lock_remaining(store) > 0:
            minutes = max(1, (_lock_remaining(store) + 59) // 60)
            return "", f"配对码已锁定，约 {minutes} 分钟后可再试。"
        now = _now()
        window_start = now - timedelta(seconds=60)
        recent = [item for item in store["code_generations"] if (_parse(item) or window_start) >= window_start]
        if len(recent) >= CODE_GENERATIONS_PER_MINUTE:
            return "", "一分钟内最多生成 3 次配对码，请稍等再试。"
        code = "".join(secrets.choice("0123456789") for _ in range(PAIR_CODE_LENGTH))
        expires_at = now + timedelta(seconds=PAIR_CODE_TTL_SECONDS)
        store["code_generations"] = recent + [_iso(now)]
        store["code"] = {
            "hash": _sha256(code),
            "created_at": _iso(now),
            "expires_at": _iso(expires_at),
        }
        save_store(store)
        # 明文只进内存缓存，供设置页反复显示/倒计时（见 current_plaintext_code 的说明）。
        _remember_plaintext(code, expires_at, store["code"]["created_at"])
        return code, ""


def pending_code_info() -> dict:
    """设置页展示用：当前是否还有效、几秒后过期（不暴露码本身）。"""
    store = load_store()
    code = store.get("code") or {}
    expires = _parse(code.get("expires_at", ""))
    if expires is None:
        return {"active": False, "seconds_left": 0, "created_at": ""}
    left = int((expires - _now()).total_seconds())
    return {
        "active": left > 0,
        "seconds_left": max(left, 0),
        "created_at": code.get("created_at", ""),
    }


def _register_failure(store: dict) -> str:
    failures = store["failures"]
    failures["count"] = int(failures.get("count", 0) or 0) + 1
    if failures["count"] >= MAX_FAILED_ATTEMPTS:
        failures["count"] = 0
        failures["locked_until"] = _iso(_now() + timedelta(seconds=LOCK_SECONDS))
        return f"连续输错 5 次，已锁定 {LOCK_SECONDS // 60} 分钟。"
    remaining = MAX_FAILED_ATTEMPTS - failures["count"]
    return f"配对码不正确，还可以再试 {remaining} 次。"


def _new_device(label: str, ip: str, platform: str = "", uid: str = "") -> tuple[dict, str]:
    token = secrets.token_urlsafe(32)
    device = {
        "id": secrets.token_hex(8),
        "token_sha256": _sha256(token),
        "label": (label or "").strip()[:40] or "手机",
        # platform 决定设置页怎么称呼它：`app` = 原生客户端，`browser` = 浏览器。
        # 没有它的话，设置页只能从 UA 猜，而原生端的 UA 里同样带 "android"，
        # 于是一个 App 和一个浏览器会显示成两条一模一样的「Android 手机」。
        "platform": normalize_platform(platform),
        "paired_at": _iso(_now()),
        "last_seen_at": "",
        "last_ip": (ip or "").strip()[:64],
        # 客户端上报的稳定身份（可伪造，只用于合并展示，不参与认证）。
        "uid": normalize_device_uid(uid),
        "last_sync_at": "",
    }
    return device, token


PLATFORM_APP = "app"
PLATFORM_BROWSER = "browser"


def normalize_platform(value: str) -> str:
    """把各种来源的平台标识收敛成两个值。

    配对端（原生 App / HTML 页面）自己报，服务端不信任何花哨输入：
    只认 `app`，其余一律当 `browser`。这样即使有人伪造，也只会被归到浏览器，
    不会伪造出「这是店主的手机 App」这种更危险的身份。
    """
    return PLATFORM_APP if str(value or "").strip().lower() == PLATFORM_APP else PLATFORM_BROWSER


def platform_label(platform: str) -> str:
    return "手机 App" if normalize_platform(platform) == PLATFORM_APP else "浏览器"


def verify_pair_code(
    code: str, *, label: str = "", ip: str = "", platform: str = "", uid: str = ""
) -> tuple[str, str]:
    """校验配对码。返回 `(cookie_value, error)`：成功时 error 为空串。

    `uid` 非空且已有同 uid 的设备时**原地更新**（换 token、更新 label/时间），
    不新增记录 —— 这是「同一台手机重复配对长出两条」的修法。旧记录 `id` 保持不变，
    所以桌面端「撤销这台」的表单值仍然有效。

    整个「校验 → 记失败次数 / 追加设备 → 保存」是**一个事务**（`_STORE_LOCK`）：
    两台手机同时提交各自的码时，没有锁会让后一个写者用自己那份过期快照覆盖前一台
    刚追加的设备记录 —— 表现就是「只配对成功一台，另一台要重配」。
    """
    cleaned = "".join(ch for ch in str(code or "") if ch.isdigit())
    with _STORE_LOCK:
        store = load_store()
        remaining = _lock_remaining(store)
        if remaining > 0:
            minutes = max(1, (remaining + 59) // 60)
            return "", f"配对码已锁定，约 {minutes} 分钟后可再试。"
        if len(cleaned) != PAIR_CODE_LENGTH:
            return "", "请输入电脑上显示的 6 位数字。"

        stored = store.get("code") or {}
        expires = _parse(stored.get("expires_at", ""))
        if not stored.get("hash") or expires is None or expires <= _now():
            # 过期不记失败次数：用户只是慢了一点，不该被扣一次机会。
            return "", "配对码已过期，请在电脑的设置页重新生成。"

        if not hmac.compare_digest(_sha256(cleaned), str(stored.get("hash"))):
            message = _register_failure(store)
            save_store(store)
            return "", message

        clean_uid = normalize_device_uid(uid)
        existing_index = next(
            (
                index
                for index, item in enumerate(store["devices"])
                if clean_uid and item.get("uid") == clean_uid
            ),
            None,
        )
        if existing_index is not None:
            device, token = _renew_device(
                store["devices"][existing_index], label, ip, platform, clean_uid
            )
            # ⚠️ 必须写回列表：`_renew_device` 返回的是副本，只改副本等于**没换 token**
            # （旧 cookie 继续有效，而界面上看着像重配成功）。
            store["devices"][existing_index] = device
        else:
            device, token = _new_device(label, ip, platform, clean_uid)
            store["devices"].append(device)
        store["code"] = None
        store["failures"] = {"count": 0, "locked_until": ""}
        save_store(store)
        # 码已被用掉：内存里的明文也必须立刻作废，否则设置页还会显示一个永远无效的码。
        _remember_plaintext("", None)
        return f"{device['id']}:{token}", ""


def _renew_device(existing: dict, label: str, ip: str, platform: str, uid: str) -> tuple[dict, str]:
    """同 uid 重新配对：**保留 id / last_seen_at / last_sync_at**，换 token 与其它展示字段。

    必须换 token：旧 cookie 已经丢了才会重新配对；不换的话旧 cookie（如果还留在
    某台设备的浏览器里）会继续有效，等于「重新配对」没起到收敛作用。
    """
    token = secrets.token_urlsafe(32)
    device = dict(existing)
    device.update(
        {
            "token_sha256": _sha256(token),
            "label": (label or "").strip()[:40] or str(existing.get("label") or "手机"),
            "platform": normalize_platform(platform),
            "paired_at": _iso(_now()),
        }
    )
    if ip:
        device["last_ip"] = ip[:64]
    device["uid"] = uid
    device.setdefault("last_sync_at", "")
    return device, token


def device_from_cookie(raw: str | None) -> dict | None:
    """cookie → 设备记录；token 与文件里的 sha256 比对，不匹配即视为无效。"""
    raw = (raw or "").strip()
    if ":" not in raw:
        return None
    device_id, token = raw.split(":", 1)
    if not device_id or not token:
        return None
    for device in load_store()["devices"]:
        if device.get("id") != device_id:
            continue
        if hmac.compare_digest(_sha256(token), str(device.get("token_sha256", ""))):
            return dict(device)
        return None
    return None


# 最后访问时间不必每个请求都落盘：内存里攒着，间隔超过这个秒数才写一次。
_LAST_SEEN_FLUSH_SECONDS = 20
_last_seen_cache: dict[str, datetime] = {}


def remember_success(device: dict | None, ip: str = "") -> None:
    """记「最后一次手机成功请求」。桌面端诊断面板据此判断是哪一侧出问题。"""
    if not device:
        return
    device_id = str(device.get("id") or "")
    if not device_id:
        return
    now = _now()
    with _STORE_LOCK:
        previous = _last_seen_cache.get(device_id)
        if previous is not None and (now - previous).total_seconds() < _LAST_SEEN_FLUSH_SECONDS:
            return
        _last_seen_cache[device_id] = now
        store = load_store()
        for item in store["devices"]:
            if item.get("id") == device_id:
                item["last_seen_at"] = _iso(now)
                if ip:
                    item["last_ip"] = ip[:64]
                break
        store["last_success_at"] = _iso(now)
        save_store(store)


# 快照（全量同步）是「关电脑前让手机拉一遍」的动作，频率远低于普通查询，
# 所以它**每次都落盘**：桌面端的关闭提醒要显示的正是「一次真正的同步」的时间，
# 节流会让这个时间比实际旧几分钟，正好落进「到底同没同步」的疑心里。
def mark_synced(device: dict | None) -> str:
    """记「这台设备最后一次拉全量快照」。返回写入的时间串（空串 = 没记）。"""
    if not device:
        return ""
    device_id = str(device.get("id") or "")
    if not device_id:
        return ""
    moment = _iso(_now())
    with _STORE_LOCK:
        store = load_store()
        for item in store["devices"]:
            if item.get("id") == device_id:
                item["last_sync_at"] = moment
                break
        store["last_success_at"] = moment
        save_store(store)
        _last_seen_cache[device_id] = _now()
        return moment


def list_devices() -> list[dict]:
    devices = []
    for device in load_store()["devices"]:
        uid = normalize_device_uid(device.get("uid", ""))
        devices.append(
            {
                "id": device.get("id", ""),
                "label": device.get("label", "手机"),
                # 老记录（本次改动之前配对的）没有 platform 字段，按浏览器算 ——
                # 那是当时唯一存在的客户端，猜成 App 会给出错误信息。
                "platform": normalize_platform(device.get("platform", "")),
                "platform_label": platform_label(device.get("platform", "")),
                "paired_at": device.get("paired_at", ""),
                "last_seen_at": device.get("last_seen_at", ""),
                # 空 uid = 升级前配的旧记录（或未上报身份的客户端），界面显示「旧记录」。
                "uid": uid,
                "uid_short": uid[:8] if uid else "",
                "last_sync_at": device.get("last_sync_at", ""),
                "last_ip": device.get("last_ip", ""),
            }
        )
    devices.sort(key=lambda item: item.get("paired_at", ""), reverse=True)
    return devices


def revoke_device(device_id: str) -> bool:
    with _STORE_LOCK:
        store = load_store()
        before = len(store["devices"])
        store["devices"] = [item for item in store["devices"] if item.get("id") != device_id]
        if len(store["devices"]) == before:
            return False
        save_store(store)
        return True


def revoke_all_devices() -> int:
    """撤销全部设备（设置页「全部撤销」按钮背后就这一个调用）。

    ⚠️ 旧版 docstring 写的是「换端口/关开关前必须先撤销所有设备」，**这句是错的**，
    已删：代码里从来没有这样的调用，产品也没有「改端口」的 UI（端口固定 5001，
    见 `mobile_server` 模块头）。两个监听共用同一份 `mobile_devices.json`，
    换数据根、重开开关都**不需要**撤销设备。照着那句注释去「补上撤销」，
    结果是用户每次打开开关都被静默全部踢下线、手机全要重配。

    撤销只有两个真实入口：这里与 `revoke_device`，都必须由用户在设置页显式点击。
    """
    with _STORE_LOCK:
        store = load_store()
        count = len(store["devices"])
        store["devices"] = []
        save_store(store)
        return count


def device_count() -> int:
    return len(load_store()["devices"])


def last_success_at() -> str:
    return str(load_store().get("last_success_at", "") or "")
