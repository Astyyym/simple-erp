"""Desktop startup must report backend failures rather than hide them as timeouts."""
import queue
import sqlite3
import threading
import time

import pytest

import desktop_app
from erp import db


def test_database_startup_failure_is_logged_and_reported_immediately(caplog):
    db.db_path().write_bytes(b"synthetic invalid SQLite file")
    errors = queue.Queue()
    worker = threading.Thread(target=desktop_app._run_server, args=(errors,))
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="简单ERP启动失败.*file is not a database") as caught:
        desktop_app._wait_for_server(timeout_seconds=1, startup_errors=errors)
    assert time.monotonic() - started < 0.5
    assert isinstance(caught.value.__cause__, sqlite3.DatabaseError)
    assert "本地服务启动失败" in caplog.text
    assert "Traceback" in caplog.text


def test_main_reports_real_database_failure_before_opening_webview():
    db.db_path().write_bytes(b"synthetic invalid SQLite file")
    with pytest.raises(RuntimeError, match="简单ERP启动失败.*file is not a database"):
        desktop_app.main()


def test_wait_returns_when_server_port_becomes_ready(monkeypatch):
    probes = iter((False, True))
    monkeypatch.setattr(desktop_app, "_port_is_open", lambda: next(probes))
    monkeypatch.setattr(desktop_app.time, "sleep", lambda _: None)
    desktop_app._wait_for_server(timeout_seconds=1, startup_errors=queue.Queue())


def test_wait_preserves_timeout_when_no_startup_failure_is_available(monkeypatch):
    timestamps = iter((0, 0, 1))
    monkeypatch.setattr(desktop_app.time, "time", lambda: next(timestamps))
    monkeypatch.setattr(desktop_app.time, "sleep", lambda _: None)
    monkeypatch.setattr(desktop_app, "_port_is_open", lambda: False)
    with pytest.raises(RuntimeError, match="简单ERP启动超时"):
        desktop_app._wait_for_server(timeout_seconds=1, startup_errors=queue.Queue())


def test_startup_failure_is_not_masked_by_an_open_port(monkeypatch):
    errors = queue.Queue()
    error = OSError("synthetic bind failure")
    errors.put(error)
    monkeypatch.setattr(desktop_app, "_port_is_open", lambda: True)
    with pytest.raises(RuntimeError, match="synthetic bind failure") as caught:
        desktop_app._wait_for_server(startup_errors=errors)
    assert caught.value.__cause__ is error


def test_direct_server_call_preserves_original_exception():
    db.db_path().write_bytes(b"synthetic invalid SQLite file")
    with pytest.raises(sqlite3.DatabaseError, match="file is not a database"):
        desktop_app._run_server()
