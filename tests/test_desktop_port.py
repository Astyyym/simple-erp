"""桌面壳端口解析：`ERP_PORT` > `config.json` 的 `port` > 5000，host 恒回环。

背景（2026-10-12 发版冒烟发现）：端口原先在 `APP_URL` / `_run_server` /
`_port_is_open` 三处**写死 5000**，既不读 `config.json` 的 `port` 也不读
`ERP_PORT`——隔离冒烟设了 5099，EXE 照样起在 5000，只能让 5000 空着等它。
"""
from __future__ import annotations

import json
import socket

import pytest

import desktop_app as desk
from erp import config as config_module


def test_env_port_wins_over_config(monkeypatch):
    monkeypatch.setenv("ERP_PORT", "5099")
    monkeypatch.setattr(config_module, "load_config", lambda: {"port": 5000})
    assert desk.resolve_port() == 5099


def test_config_port_used_when_env_absent(monkeypatch):
    monkeypatch.delenv("ERP_PORT", raising=False)
    monkeypatch.setattr(config_module, "load_config", lambda: {"port": 5123})
    assert desk.resolve_port() == 5123


def test_default_port_when_neither_env_nor_config(monkeypatch):
    monkeypatch.delenv("ERP_PORT", raising=False)
    monkeypatch.setattr(config_module, "load_config", lambda: {})
    assert desk.resolve_port() == 5000


def test_config_read_failure_falls_back_to_default(monkeypatch):
    """配置读不到不能让桌面壳起不来，回退默认端口。"""
    monkeypatch.delenv("ERP_PORT", raising=False)

    def boom():
        raise FileNotFoundError("no config")

    monkeypatch.setattr(config_module, "load_config", boom)
    assert desk.resolve_port() == 5000


@pytest.mark.parametrize("bad", ["", "abc", "0", "-1", "70000", "5000.5", None])
def test_invalid_port_values_are_rejected(monkeypatch, bad):
    monkeypatch.setenv("ERP_PORT", "" if bad is None else bad)
    monkeypatch.setattr(config_module, "load_config", lambda: {"port": 5000})
    assert desk.resolve_port() == 5000


def test_app_url_uses_resolved_port_and_loopback_host(monkeypatch):
    monkeypatch.setenv("ERP_PORT", "5099")
    assert desk._app_url() == "http://127.0.0.1:5099"
    assert desk._app_url(5123) == "http://127.0.0.1:5123"


def test_app_url_never_takes_host_from_config(monkeypatch):
    """安全约束：产品永久免登录，配置里写 0.0.0.0 也绝不能暴露到局域网。"""
    monkeypatch.delenv("ERP_PORT", raising=False)
    monkeypatch.setattr(config_module, "load_config",
                        lambda: {"port": 5099, "host": "0.0.0.0"})
    assert desk.resolve_port() == 5099
    assert desk._app_url() == "http://127.0.0.1:5099"
    assert "0.0.0.0" not in desk._app_url()


def test_run_server_binds_the_resolved_port(monkeypatch):
    """真实绑定必须用解析出的端口（不是写死 5000）。"""
    monkeypatch.setenv("ERP_PORT", "5098")
    captured = {}

    def fake_serve(app, host=None, port=None, threads=None):
        captured.update({"host": host, "port": port})

    monkeypatch.setattr(desk, "serve", fake_serve)
    desk._run_server(app=object())
    assert captured == {"host": "127.0.0.1", "port": 5098}


def test_port_is_open_stays_zero_arg_callable(monkeypatch):
    """既有启动测试用零参 lambda 替换 `_port_is_open`，签名不能要求实参。"""
    monkeypatch.setenv("ERP_PORT", "5098")
    monkeypatch.setattr(desk, "resolve_port", lambda: 5098)
    # 零参调用不得抛 TypeError
    assert desk._port_is_open() is False or desk._port_is_open() is True


def test_port_is_open_probes_the_given_port(monkeypatch):
    """显式端口优先于解析结果（main() 把同一端口贯穿探测/绑定/加载）。"""
    monkeypatch.setenv("ERP_PORT", "5098")
    seen = {}

    class FakeSock:
        def settimeout(self, _): ...
        def connect_ex(self, addr):
            seen["addr"] = addr
            return 1
        def close(self): ...

    monkeypatch.setattr(desk.socket, "socket", lambda *a, **k: FakeSock())
    assert desk._port_is_open(port=5097) is False
    assert seen["addr"] == ("127.0.0.1", 5097)


def test_resolved_port_actually_accepts_connections(monkeypatch):
    """端到端：按解析端口真起一个 socket，`_port_is_open` 能探到。"""
    monkeypatch.delenv("ERP_PORT", raising=False)
    monkeypatch.setattr(config_module, "load_config", lambda: {"port": 5096})
    port = desk.resolve_port()
    assert port == 5096
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        assert desk._port_is_open(port=port) is True
        assert desk._port_is_open(port=5095) is False
