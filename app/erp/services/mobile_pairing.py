"""手机只读端的配对门：6 位配对码 + 设备 token + Set-Cookie 会话。

设计约束（见 `docs/需求/2026-10-17_安卓只读端架构决策.md` §4）：

- **唯一的写端**就是这里，且只写 `data/mobile_devices.json`，**绝不写 `erp.db`**。
  除配对提交本身，手机端所有查询都走只读连接（`services/readonly_queries.py`）。
- 浏览器方案下无法给每个页面请求加 header，所以会话用 **Set-Cookie**。
- cookie 里带设备 id + 明文 token；文件里只存 token 的 sha256，比较走
  `hmac.compare_digest`。手机丢失 → 桌面端撤销设备 → 立刻失联（阶段一不落本地数据）。
- 6 位码：每分钟最多生成 3 次、有效期 120 秒、连续错 5 次锁 5 分钟。

时间一律存本地时间 ISO 串（与项目其它 JSON 配置一致），便于设置页直接展示。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from erp.config import project_path

COOKIE_NAME = "erp_mobile"

PAIR_CODE_LENGTH = 6
PAIR_CODE_TTL_SECONDS = 120
CODE_GENERATIONS_PER_MINUTE = 3
MAX_FAILED_ATTEMPTS = 5
LOCK_SECONDS = 300

STORE_VERSION = 1


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
    path = store_path()
    if not path.exists():
        return _empty_store()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_store()
    store = _empty_store()
    if isinstance(payload, dict):
        store.update({key: payload.get(key, store[key]) for key in store})
    if not isinstance(store.get("devices"), list):
        store["devices"] = []
    if not isinstance(store.get("code_generations"), list):
        store["code_generations"] = []
    if not isinstance(store.get("failures"), dict):
        store["failures"] = {"count": 0, "locked_until": ""}
    return store


def save_store(store: dict) -> None:
    """原子写：先写临时文件再 `replace`，避免半截 JSON 让配对门整个失效。"""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


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


def generate_pair_code() -> tuple[str, str]:
    """生成一次 6 位配对码，返回 `(code, error)`；被限速或锁定时 code 为空串。"""
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
    store["code_generations"] = recent + [_iso(now)]
    store["code"] = {
        "hash": _sha256(code),
        "created_at": _iso(now),
        "expires_at": _iso(now + timedelta(seconds=PAIR_CODE_TTL_SECONDS)),
    }
    save_store(store)
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


def _new_device(label: str, ip: str, platform: str = "") -> tuple[dict, str]:
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
    code: str, *, label: str = "", ip: str = "", platform: str = ""
) -> tuple[str, str]:
    """校验配对码。返回 `(cookie_value, error)`：成功时 error 为空串。"""
    cleaned = "".join(ch for ch in str(code or "") if ch.isdigit())
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

    device, token = _new_device(label, ip, platform)
    store["devices"].append(device)
    store["code"] = None
    store["failures"] = {"count": 0, "locked_until": ""}
    save_store(store)
    return f"{device['id']}:{token}", ""


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


def list_devices() -> list[dict]:
    devices = []
    for device in load_store()["devices"]:
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
                "last_ip": device.get("last_ip", ""),
            }
        )
    devices.sort(key=lambda item: item.get("paired_at", ""), reverse=True)
    return devices


def revoke_device(device_id: str) -> bool:
    store = load_store()
    before = len(store["devices"])
    store["devices"] = [item for item in store["devices"] if item.get("id") != device_id]
    if len(store["devices"]) == before:
        return False
    save_store(store)
    return True


def revoke_all_devices() -> int:
    """换端口/关开关前必须先撤销所有设备（端口是 base_url 的一部分）。"""
    store = load_store()
    count = len(store["devices"])
    store["devices"] = []
    save_store(store)
    return count


def device_count() -> int:
    return len(load_store()["devices"])


def last_success_at() -> str:
    return str(load_store().get("last_success_at", "") or "")
