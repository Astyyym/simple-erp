from erp.db import get_db


def log_action(action: str, target_type: str, target_id: str | int, summary: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO audit_logs(action, target_type, target_id, summary) VALUES (?, ?, ?, ?)",
            (action, target_type, str(target_id), summary),
        )
