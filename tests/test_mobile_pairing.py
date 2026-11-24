"""配对门测试：6 位码限速/锁定、token 哈希比对、cookie 会话、撤销设备。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from erp.services import mobile_pairing


def _reset() -> None:
    store = mobile_pairing._empty_store()
    mobile_pairing.save_store(store)


@pytest.fixture(autouse=True)
def clean_store():
    _reset()
    mobile_pairing._last_seen_cache.clear()
    yield
    _reset()
    mobile_pairing._last_seen_cache.clear()


def test_pair_code_roundtrip_returns_device_cookie():
    code, error = mobile_pairing.generate_pair_code()
    assert error == "" and len(code) == 6 and code.isdigit()

    wrong_cookie, wrong_error = mobile_pairing.verify_pair_code("000000" if code != "000000" else "111111")
    assert wrong_cookie == ""
    assert "配对码不正确" in wrong_error

    cookie_value, error = mobile_pairing.verify_pair_code(code, label="小手机")
    assert error == ""
    device = mobile_pairing.device_from_cookie(cookie_value)
    assert device is not None and device["label"] == "小手机"
    assert mobile_pairing.device_count() == 1


def test_code_is_single_use():
    code, _ = mobile_pairing.generate_pair_code()
    assert mobile_pairing.verify_pair_code(code)[1] == ""
    second_cookie, second_error = mobile_pairing.verify_pair_code(code)
    assert second_cookie == ""
    assert "过期" in second_error


def test_expired_code_is_rejected_without_counting_a_failure():
    code, _ = mobile_pairing.generate_pair_code()
    store = mobile_pairing.load_store()
    store["code"]["expires_at"] = (datetime.now() - timedelta(seconds=5)).isoformat(timespec="seconds")
    mobile_pairing.save_store(store)

    cookie, error = mobile_pairing.verify_pair_code(code)
    assert cookie == "" and "过期" in error
    assert mobile_pairing.load_store()["failures"]["count"] == 0


def test_five_wrong_attempts_lock_for_five_minutes():
    code, _ = mobile_pairing.generate_pair_code()
    wrong = "000000" if code != "000000" else "111111"
    for attempt in range(4):
        _, error = mobile_pairing.verify_pair_code(wrong)
        assert "还可以再试" in error, attempt
    _, error = mobile_pairing.verify_pair_code(wrong)
    assert "锁定" in error
    assert mobile_pairing.lock_remaining_seconds() > 0
    # 锁定期间连正确的码也不接受
    _, locked_error = mobile_pairing.verify_pair_code(code)
    assert "锁定" in locked_error


def test_code_generation_is_rate_limited_per_minute():
    for _ in range(mobile_pairing.CODE_GENERATIONS_PER_MINUTE):
        code, error = mobile_pairing.generate_pair_code()
        assert error == "" and code
    code, error = mobile_pairing.generate_pair_code()
    assert code == ""
    assert "最多生成 3 次" in error


def test_cookie_token_must_match_stored_hash():
    code, _ = mobile_pairing.generate_pair_code()
    cookie_value, _ = mobile_pairing.verify_pair_code(code)
    device_id, token = cookie_value.split(":", 1)

    assert mobile_pairing.device_from_cookie(f"{device_id}:{token}x") is None
    assert mobile_pairing.device_from_cookie(f"{device_id}:") is None
    assert mobile_pairing.device_from_cookie("garbage") is None
    assert mobile_pairing.device_from_cookie(None) is None


def test_revoke_device_invalidates_cookie_immediately():
    code, _ = mobile_pairing.generate_pair_code()
    cookie_value, _ = mobile_pairing.verify_pair_code(code)
    device = mobile_pairing.device_from_cookie(cookie_value)
    assert mobile_pairing.revoke_device(device["id"]) is True
    assert mobile_pairing.device_from_cookie(cookie_value) is None
    assert mobile_pairing.revoke_device(device["id"]) is False


def test_revoke_all_devices_returns_count():
    for _ in range(2):
        code, _ = mobile_pairing.generate_pair_code()
        mobile_pairing.verify_pair_code(code)
    assert mobile_pairing.device_count() == 2
    assert mobile_pairing.revoke_all_devices() == 2
    assert mobile_pairing.device_count() == 0


def test_remember_success_records_last_seen_throttled():
    code, _ = mobile_pairing.generate_pair_code()
    cookie_value, _ = mobile_pairing.verify_pair_code(code)
    device = mobile_pairing.device_from_cookie(cookie_value)
    mobile_pairing.remember_success(device, "192.168.10.9")
    store = mobile_pairing.load_store()
    assert store["last_success_at"]
    assert store["devices"][0]["last_ip"] == "192.168.10.9"
    assert mobile_pairing.last_success_at() == store["last_success_at"]


def test_store_lives_under_data_root():
    path = mobile_pairing.store_path()
    assert path.name == "mobile_devices.json"
    assert path.parent.name == "data"


# ---------------------------------------------------------------------------
# 设备身份 uid（2026-10-24）：同一台手机重复配对不能长出两条
# ---------------------------------------------------------------------------

def _pair(uid: str = "", label: str = "手机", platform: str = "app") -> str:
    """配一台设备。**每次配对前清掉限速窗口**：一个用例里要配好几次，
    而「一分钟最多生成 3 次码」是另一条契约（`test_code_generation_is_rate_limited_per_minute`），
    不该在这里误伤。"""
    store = mobile_pairing.load_store()
    store["code_generations"] = []
    mobile_pairing.save_store(store)
    code, error = mobile_pairing.generate_pair_code()
    assert not error, error
    cookie, error = mobile_pairing.verify_pair_code(
        code, label=label, platform=platform, uid=uid
    )
    assert not error, error
    return cookie


def test_same_uid_repairing_keeps_one_record():
    """同一台手机重复配对（改地址/清 cookie 后再配）→ 仍然只有一条。

    这是用户实测报的缺陷：桌面端出现两条一模一样的「手机 App」。
    """
    _pair(uid="abc123def456", label="Xiaomi 2201123C")
    first_id = mobile_pairing.list_devices()[0]["id"]
    _pair(uid="abc123def456", label="Xiaomi 2201123C")
    second = _pair(uid="abc123def456", label="Xiaomi 2201123C")

    devices = mobile_pairing.list_devices()
    assert len(devices) == 1, "同 uid 重配必须合并，不能新增"
    assert devices[0]["id"] == first_id, "记录 id 必须保持不变（撤销表单靠它）"
    # 旧 cookie 必须失效：重配就要换 token，否则「重新配对」没有收敛作用。
    assert mobile_pairing.device_from_cookie(second) is not None


def test_same_uid_repairing_invalidates_previous_cookie():
    first = _pair(uid="uidchurn00001")
    second = _pair(uid="uidchurn00001")
    assert mobile_pairing.device_from_cookie(first) is None
    assert mobile_pairing.device_from_cookie(second) is not None


def test_different_uids_still_create_separate_devices():
    _pair(uid="phoneone00001", label="手机 A")
    _pair(uid="phonetwo00002", label="手机 B")
    assert mobile_pairing.device_count() == 2


def test_blank_uid_keeps_legacy_append_behaviour():
    """没有 uid 的客户端（旧版 App / 第三方）能配对，但不会自动合并 —— 这是有意保留的兼容。"""
    _pair(uid="")
    _pair(uid="")
    assert mobile_pairing.device_count() == 2


def test_invalid_uid_is_ignored_not_rejected():
    """非法 uid 当空串处理：配对**成功**，只是不参与合并（不能因为一个字段阻止配对）。"""
    for bad in ("ab", "有中文的UID", "x" * 80, "with space", "<script>"):
        cookie = _pair(uid=bad)
        assert mobile_pairing.device_from_cookie(cookie) is not None
        assert mobile_pairing.normalize_device_uid(bad) == ""


def test_normalize_device_uid_accepts_url_safe_tokens():
    for good in ("abc123", "A" * 64, "uid-with_dash", "0123456789abcdef"):
        assert mobile_pairing.normalize_device_uid(good) == good


def test_legacy_store_without_uid_still_loads():
    """v1 的库（没有任何 uid 字段）必须能读，且旧记录不被删。"""
    store = mobile_pairing._empty_store()
    store["version"] = 1
    store["devices"] = [
        {
            "id": "legacy0001",
            "token_sha256": mobile_pairing._sha256("tok"),
            "label": "手机",
            "paired_at": "2026-10-18T09:39:09",
            "last_seen_at": "",
            "last_ip": "127.0.0.1",
        }
    ]
    mobile_pairing.save_store(store)

    loaded = mobile_pairing.load_store()
    assert loaded["version"] == mobile_pairing.STORE_VERSION
    assert len(loaded["devices"]) == 1
    listed = mobile_pairing.list_devices()[0]
    assert listed["uid"] == "" and listed["uid_short"] == ""
    assert mobile_pairing.device_from_cookie("legacy0001:tok") is not None


def test_store_with_duplicate_uids_is_collapsed_on_load():
    """升级前已经长出来的重复 uid 记录：读取时就该收敛成一条（不用等用户再配一次）。"""
    store = mobile_pairing._empty_store()
    store["devices"] = [
        {
            "id": "old",
            "token_sha256": mobile_pairing._sha256("a"),
            "label": "手机 App",
            "platform": "app",
            "uid": "sameuid12345",
            "paired_at": "2026-10-24T19:29:33",
            "last_seen_at": "2026-10-24T19:35:12",
            "last_ip": "192.168.10.3",
        },
        {
            "id": "new",
            "token_sha256": mobile_pairing._sha256("b"),
            "label": "手机 App",
            "platform": "app",
            "uid": "sameuid12345",
            "paired_at": "2026-10-24T19:36:23",
            "last_seen_at": "2026-10-24T19:36:24",
            "last_ip": "192.168.10.3",
        },
    ]
    mobile_pairing.save_store(store)

    devices = mobile_pairing.list_devices()
    assert len(devices) == 1
    # 留配对时间更晚的那条，但 last_seen 取两者较大值 —— 不能把「真的连上过」的时间丢掉。
    assert devices[0]["id"] == "new"
    assert devices[0]["last_seen_at"] == "2026-10-24T19:36:24"


def test_mark_synced_records_time_and_shows_in_list():
    cookie = _pair(uid="syncuid00001")
    device = mobile_pairing.device_from_cookie(cookie)
    moment = mobile_pairing.mark_synced(device)
    assert moment
    listed = mobile_pairing.list_devices()[0]
    assert listed["last_sync_at"] == moment


def test_mark_synced_without_device_is_safe():
    assert mobile_pairing.mark_synced(None) == ""
    assert mobile_pairing.mark_synced({}) == ""


# ---------------------------------------------------------------------------
# 并发回归：两个监听同进程，6 个写者必须串行（S1）
# ---------------------------------------------------------------------------

def test_concurrent_pair_and_sync_loses_nothing(monkeypatch):
    """两个线程各做 100 次「配对 + mark_synced」：设备数必须正好等于成功配对数。

    为什么必须有这条：`mobile_devices.json` 的 6 个写者原来是清一色的
    「load_store() → 就地改 → save_store()」，没有锁。两个监听在**同一进程**里
    并发（主服务 threads=8 + 第二监听 threads=4），症状是设备记录凭空消失、
    撤销被并发写回、`os.replace` 撞 `PermissionError`（mark_synced 抛异常 →
    快照请求 500）。把 `mobile_pairing._STORE_LOCK` 临时换成 `nullcontext()`
    跑一遍这条会红 —— 这就是它的价值，不能因为「现在有锁了」删掉。

    配对码是**单次性**的（只存一个 hash，用掉即作废），所以「生成 + 提交」必须
    放在同一把测试闸门里，否则失败来自业务语义而不是我们要验证的锁。
    `mark_synced` 故意留在闸门外：它照样与另一个线程的配对写并发。
    """
    import threading
    import time

    # 一分钟三次的限速在这里只会让用例变慢，与并发无关，直接放开。
    monkeypatch.setattr(mobile_pairing, "CODE_GENERATIONS_PER_MINUTE", 10**9)

    thread_count, per_thread = 2, 100
    gate = threading.Lock()  # 只保护「配对码生成 + 提交」，不保护 mark_synced
    barrier = threading.Barrier(thread_count)
    paired: list[str] = []
    errors: list[BaseException] = []
    collect_lock = threading.Lock()

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=30)
            for round_index in range(per_thread):
                with gate:
                    code, error = mobile_pairing.generate_pair_code()
                    if error:
                        raise AssertionError(f"生成配对码失败：{error}")
                    cookie, error = mobile_pairing.verify_pair_code(
                        code, label=f"并发手机{index}-{round_index}", ip="192.168.10.9"
                    )
                if error:
                    raise AssertionError(f"配对失败：{error}")
                device = mobile_pairing.device_from_cookie(cookie)
                if device is None:
                    raise AssertionError("刚配对成功的设备立刻查不到（写丢失）")
                mobile_pairing.mark_synced(device)
                with collect_lock:
                    paired.append(cookie)
        except BaseException as exc:  # noqa: BLE001 — 任何异常都要带回主线程断言
            with collect_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(index,), name=f"pair-{index}", daemon=True)
        for index in range(thread_count)
    ]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=90)
    elapsed = time.monotonic() - started

    assert not any(thread.is_alive() for thread in threads), "并发用例超时"
    assert errors == [], errors
    assert len(paired) == thread_count * per_thread
    # 少了 = 丢更新（配对记录被并发的 mark_synced 覆盖）；多了 = 同一台被重复追加。
    assert mobile_pairing.device_count() == len(paired)
    # 需求：这条用例必须在 2 分钟内跑完（含 fsync + .bak 的文件写入）。
    assert elapsed < 120, f"太慢：{elapsed:.1f}s"


# ---------------------------------------------------------------------------
# 坏文件不静默（S2）
# ---------------------------------------------------------------------------

def test_missing_store_is_still_an_empty_store(caplog):
    """「文件不存在」是正常语义（首次运行 / 换数据根）：不告警、不造文件。"""
    import logging

    path = mobile_pairing.store_path()
    path.unlink(missing_ok=True)
    with caplog.at_level(logging.WARNING):
        store = mobile_pairing.load_store()
    assert store["devices"] == []
    assert caplog.records == []
    assert not path.exists()


def test_corrupt_store_is_archived_logged_and_recovered_from_backup(caplog):
    """半截 JSON 不能被静默当成空表：留档 `.corrupt-*`、告警、并从 `.bak` 恢复。

    真事故的链条是：某个写者拿到空表 → 把空表写回 → 坏文件被固化 →
    用户看到「所有手机同时掉配对」，而事后连取证的东西都没有。
    """
    import logging

    cookie = _pair(label="要保住的手机", uid="keepuid00001")
    path = mobile_pairing.store_path()
    assert mobile_pairing.device_count() == 1
    # save_store 前会留一份上一版副本，所以此时 .bak 是存在的
    backup = path.with_name(path.name + mobile_pairing._BACKUP_SUFFIX)
    assert backup.exists()
    # 再存一次，让 .bak 里是「有这台手机」的那一版
    mobile_pairing.save_store(mobile_pairing.load_store())
    assert backup.exists()

    path.write_text('{"version": 2, "devices": [', encoding="utf-8")  # 半截 JSON
    with caplog.at_level(logging.WARNING):
        store = mobile_pairing.load_store()

    assert [device["label"] for device in store["devices"]] == ["要保住的手机"]
    assert mobile_pairing.device_from_cookie(cookie) is not None
    # 坏文件留档（不覆盖、带时间戳），且日志里说清了路径
    corrupt = list(path.parent.glob(path.name + ".corrupt-*"))
    assert len(corrupt) == 1
    # 留档的是**原来那份坏内容**，不是被截断/清空后的东西
    assert corrupt[0].read_text(encoding="utf-8") == '{"version": 2, "devices": ['
    assert any("设备登记文件" in record.getMessage() for record in caplog.records)
    # 恢复结果必须写回磁盘：只在内存里恢复，下一个请求又会读到空表
    assert mobile_pairing.device_count() == 1


def test_corrupt_store_without_backup_does_not_vanish_silently(caplog):
    """连 .bak 都没有时：仍然留档 + 告警，然后才按空表处理（用户能取证）。"""
    import logging

    path = mobile_pairing.store_path()
    backup = path.with_name(path.name + mobile_pairing._BACKUP_SUFFIX)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", encoding="utf-8")
    backup.unlink(missing_ok=True)

    with caplog.at_level(logging.WARNING):
        store = mobile_pairing.load_store()

    assert store["devices"] == []
    assert list(path.parent.glob(path.name + ".corrupt-*"))
    assert any(record.levelno == logging.WARNING for record in caplog.records)
