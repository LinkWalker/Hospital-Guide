"""强制重灌种子数据：清空知识表后从 JSON 重新灌入。

用法：python -m scripts.reseed
注意：仅清空种子知识表（departments / symptom_rules / high_risk_signals /
hospitals / knowledge_docs），不影响会话数据（会话存储在内存/Redis）。
规则/高危词 JSON 更新后运行本脚本即可生效。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import get_connection
from app.db.seed import seed_all

# 删除顺序注意外键：symptom_rules 引用 departments，须先删子表再删被引用表
_TABLES = ["symptom_rules", "high_risk_signals", "hospitals", "knowledge_docs", "departments"]


def reseed() -> None:
    conn = get_connection()
    try:
        for t in _TABLES:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()
    seed_all()

    conn = get_connection()
    try:
        for t in _TABLES:
            n = conn.execute(f"SELECT COUNT(1) FROM {t}").fetchone()[0]
            print(f"  {t}: {n} 行")
    finally:
        conn.close()


if __name__ == "__main__":
    reseed()
    print("重灌完成。")
