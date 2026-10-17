import os
from erp import create_app

app = create_app()

if __name__ == "__main__":
    host = app.config["ERP_CONFIG"].get("host", "127.0.0.1")
    port = int(os.environ.get("ERP_PORT", app.config["ERP_CONFIG"].get("port", 5000)))
    # 源码开发模式：手机只读端开关打开时一并起第二监听（5001），退出时关掉。
    from erp.mobile_server import start_mobile_listener, stop_mobile_listener

    if app.config["ERP_CONFIG"].get("mobile_access_enabled"):
        started, listen_error = start_mobile_listener()
        if not started:
            print(f"手机访问未启动：{listen_error}")
    try:
        app.run(host=host, port=port, debug=bool(app.config["ERP_CONFIG"].get("debug", False)))
    finally:
        stop_mobile_listener()
