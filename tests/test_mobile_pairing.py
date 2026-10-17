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
