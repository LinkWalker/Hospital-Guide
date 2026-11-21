"""SQLite 数据库连接与初始化（标准库 sqlite3，零额外依赖）。

数据库文件：data/triage.db
初始化：启动时若库不存在则建表 + 灌入种子数据。
"""
import os
import sqlite3

# 工程根目录 = 本文件上两级
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
DB_PATH = os.path.join(_DATA_DIR, "triage.db")
_SCHEMA_PATH = os.path.join(_PROJECT_ROOT, "app", "knowledge", "schema.sql")


def get_connection() -> sqlite3.Connection:
    """获取一个 SQLite 连接，开启外键约束、Row 工厂。"""
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _ensure_hospital_columns(conn: sqlite3.Connection) -> None:
    """旧库平滑升级：为 hospitals 表补地址/经纬度列（CREATE TABLE IF NOT EXISTS 不会加列）。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(hospitals)").fetchall()}
    additions = {
        "address": "TEXT",
        "latitude": "REAL",
        "longitude": "REAL",
    }
    for name, ddl in additions.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE hospitals ADD COLUMN {name} {ddl}")


def init_db() -> None:
    """建表 / 升级表结构（幂等），再灌种子数据。

    之前是“数据库文件存在就整段跳过”，导致新增表（如会话历史）无法加到旧库。
    现在每次启动都执行 `CREATE TABLE IF NOT EXISTS`，旧库也能平滑升级。
    """
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        sql = f.read()
    conn = get_connection()
    try:
        conn.executescript(sql)
        _ensure_hospital_columns(conn)
        conn.commit()
    finally:
        conn.close()
    # 种子函数内部逐表判断，已存在则跳过，因此可安全重复执行
    from .seed import seed_all
    seed_all()


def query_all(sql: str, params: tuple = ()) -> list:
    """查询多行。"""
    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
