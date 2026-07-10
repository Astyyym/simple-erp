import os
from erp import create_app

app = create_app()

if __name__ == "__main__":
    host = app.config["ERP_CONFIG"].get("host", "127.0.0.1")
    port = int(os.environ.get("ERP_PORT", app.config["ERP_CONFIG"].get("port", 5000)))
    app.run(host=host, port=port, debug=bool(app.config["ERP_CONFIG"].get("debug", False)))
