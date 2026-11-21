"""保存用户上次使用的医院推荐位置（单机演示级）。

只有一条记录，id 固定为 1。用于解决 file:// 与 localhost 的 localStorage
互不相通的问题：只要后端在运行，两边都能从后端取到同一个上次位置。
"""
from datetime import datetime
from typing import Any, Dict, Optional

from .database import get_connection


def get_saved_location() -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT label, address, latitude, longitude, updated_at FROM saved_location WHERE id = 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_saved_location(label: str, address: str, latitude: float, longitude: float) -> Dict[str, Any]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO saved_location (id, label, address, latitude, longitude, updated_at)
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                label = excluded.label,
                address = excluded.address,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                updated_at = excluded.updated_at
            """,
            (label, address, latitude, longitude, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "label": label,
        "address": address,
        "latitude": latitude,
        "longitude": longitude,
        "updated_at": now,
    }


def clear_saved_location() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM saved_location WHERE id = 1")
        conn.commit()
    finally:
        conn.close()
