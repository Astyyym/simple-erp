"""关闭软件前的同步提醒：闸门逻辑（每次必弹 + 绝不卡死）。

为什么不测「浮层长什么样」：那是 CSS，靠真实窗口验收。这里测的是**闸门**——
它在 UI 线程上同步跑，一旦逻辑写错，表现是「点了 × 没反应」或「关不掉」，
两种都比提醒没弹严重得多。

三条断言对应三条真实风险：
1. 第二次点 × 必须放行（浮层残留点不到时的唯一出路）；
2. 用户取消后**下一次仍然弹**（用户明确否决了「不再提醒」）；
3. 浮层调不出来时必须退回原生框，且原生框也不行时放行。
"""
from __future__ import annotations

import threading

import pytest

import desktop_app


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    """等后台线程把状态改到 predicate 为真。"""
    done = threading.Event()

    def spin() -> None:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                done.set()
                return
            time.sleep(0.01)

    threading.Thread(target=spin, daemon=True).start()
    return done.wait(timeout)


class _FakeWindow:
    """假的 webview 窗口：`evaluate_js` 按脚本内容决定返回什么。"""

    def __init__(self, js_result=True) -> None:
        self.js_result = js_result
        self.scripts: list[str] = []

    def evaluate_js(self, script: str):
        self.scripts.append(script)
        if isinstance(self.js_result, Exception):
            raise self.js_result
        return self.js_result


@pytest.fixture(autouse=True)
def clean_gate(monkeypatch):
    monkeypatch.setattr(desktop_app, "_close_gate", None)
    yield
    monkeypatch.setattr(desktop_app, "_close_gate", None)


def test_no_devices_or_switch_off_never_blocks(monkeypatch):
    """手机访问没开 / 没有设备：直接关，不拦人。"""
    gate = desktop_app.CloseGate(probe=lambda: False, notify=lambda: True)
    assert gate.should_cancel_close() is False
    assert gate.prompted is False


def test_probe_exception_is_treated_as_no_prompt():
    """探测本身炸了（配置读不到/JSON 坏了）也**不能**拦住关闭。"""

    def boom():
        raise RuntimeError("配置坏了")

    gate = desktop_app.CloseGate(probe=boom, notify=lambda: True)
    assert gate.should_cancel_close() is False


def test_first_close_cancels_and_shows_page_overlay(monkeypatch):
    fake = _FakeWindow(js_result=True)
    monkeypatch.setattr(desktop_app, "_window", fake)
    gate = desktop_app.CloseGate(probe=lambda: True, notify=lambda: True)

    assert gate.should_cancel_close() is True, "第一次点 × 必须被取消（先弹提醒）"
    assert _wait_for(lambda: bool(fake.scripts)), "后台线程应该调过 evaluate_js"
    assert "erpClosePrompt" in fake.scripts[0]
    # 浮层调出来了 → 不该再弹原生框。
    assert gate.prompted is True


def test_second_close_without_choice_goes_through(monkeypatch):
    """浮层还挂着、用户又点了一次 ×：直接放行（否则用户被卡在关不掉的程序里）。"""
    fake = _FakeWindow(js_result=True)
    monkeypatch.setattr(desktop_app, "_window", fake)
    gate = desktop_app.CloseGate(probe=lambda: True, notify=lambda: True)

    assert gate.should_cancel_close() is True
    assert gate.should_cancel_close() is False


def test_cancel_resets_so_next_close_prompts_again(monkeypatch):
    """「每次必弹」的核心断言：取消一次之后，下一次点 × 仍然弹。"""
    fake = _FakeWindow(js_result=True)
    monkeypatch.setattr(desktop_app, "_window", fake)
    gate = desktop_app.CloseGate(probe=lambda: True, notify=lambda: True)

    assert gate.should_cancel_close() is True
    gate.cancel()
    assert gate.should_cancel_close() is True, "取消后下一次必须继续提醒"
    assert _wait_for(lambda: len(fake.scripts) >= 2)


def test_confirm_lets_close_through(monkeypatch):
    fake = _FakeWindow(js_result=True)
    monkeypatch.setattr(desktop_app, "_window", fake)
    gate = desktop_app.CloseGate(probe=lambda: True, notify=lambda: True)

    assert gate.should_cancel_close() is True
    gate.confirm()
    assert gate.should_cancel_close() is False


def test_overlay_failure_falls_back_to_native_dialog_allowed(monkeypatch):
    """浮层调不出来（旧缓存没这个脚本 / 在错误页）→ 原生确认框兜底。"""
    fake = _FakeWindow(js_result=False)
    monkeypatch.setattr(desktop_app, "_window", fake)
    calls: list[str] = []

    def native() -> bool:
        calls.append("native")
        return True

    gate = desktop_app.CloseGate(probe=lambda: True, notify=native)
    assert gate.should_cancel_close() is True
    assert _wait_for(lambda: bool(calls))
    # 用户点了「确定」→ 下一次关闭放行。
    assert _wait_for(lambda: gate._decided) or gate.should_cancel_close() is False


def test_overlay_exception_falls_back_and_native_refusal_keeps_window(monkeypatch):
    """evaluate_js 抛异常 + 原生框点了取消 → 窗口留着，且下次还能再提醒。"""
    fake = _FakeWindow(js_result=RuntimeError("WebView2 没准备好"))
    monkeypatch.setattr(desktop_app, "_window", fake)
    calls: list[str] = []

    def native() -> bool:
        calls.append("native")
        return False

    gate = desktop_app.CloseGate(probe=lambda: True, notify=native)
    assert gate.should_cancel_close() is True
    assert _wait_for(lambda: bool(calls))
    assert gate._decided is False
    # 再点 ×：仍然是一个「未决定 + 已提示」的状态 → 直接放行（用户自己又点了一次）。
    assert gate.should_cancel_close() is False


def test_api_close_window_confirms_gate_then_destroys(monkeypatch):
    destroyed: list[bool] = []

    class FakeWindowWithDestroy(_FakeWindow):
        def destroy(self):
            destroyed.append(True)

    fake = FakeWindowWithDestroy(js_result=True)
    monkeypatch.setattr(desktop_app, "_window", fake)
    gate = desktop_app.CloseGate(probe=lambda: True, notify=lambda: True)
    monkeypatch.setattr(desktop_app, "_close_gate", gate)
    gate.should_cancel_close()

    api = desktop_app.DesktopApi()
    assert api.erp_close_window() is True
    assert destroyed == [True]
    assert gate.should_cancel_close() is False


def test_api_cancel_returns_true_and_clears_gate(monkeypatch):
    gate = desktop_app.CloseGate(probe=lambda: True, notify=lambda: True)
    monkeypatch.setattr(desktop_app, "_close_gate", gate)
    monkeypatch.setattr(desktop_app, "_window", _FakeWindow(js_result=True))
    gate.should_cancel_close()
    api = desktop_app.DesktopApi()
    assert api.erp_close_prompt_cancel() is True
    assert gate.prompted is False
