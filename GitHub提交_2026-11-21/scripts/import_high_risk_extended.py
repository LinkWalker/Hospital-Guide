"""批量导入扩充急症红旗信号（第二批，依据 WS/T 591-2018）。

数据来源：app/knowledge/high_risk_extended.json
特性：
- 幂等：已存在的关键词自动跳过；
- 复用现有 guide 文案（emergency / urgent 各一套）；
- 按级别统计新增条数。

用法：
    python -m scripts.import_high_risk_extended
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import get_connection, DB_PATH

_EXTENDED_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "knowledge", "high_risk_extended.json",
)

# 各级别默认引导文案（与 high_risk.json 保持一致）
_GUIDES = {
    "emergency": "您描述的症状属于紧急情况，请立即停止自行判断，尽快到最近医院的急诊科就诊；若症状严重或行动不便，请拨打 120 急救电话。",
    "urgent": "您描述的症状需要尽快就医，建议尽早到急诊科或相关专科就诊，不要拖延；如症状加重请及时拨打 120。",
}


def import_high_risk() -> dict:
    if not os.path.exists(DB_PATH):
        raise RuntimeError("数据库不存在，请先运行 init_db() 建表")

    with open(_EXTENDED_PATH, encoding="utf-8") as f:
        ext = json.load(f)

    conn = get_connection()
    stats = {}
    try:
        existing = {row[0] for row in conn.execute("SELECT keyword FROM high_risk_signals").fetchall()}
        before = conn.execute("SELECT COUNT(*) FROM high_risk_signals").fetchone()[0]

        for level, info in ext["level"].items():
            guide = _GUIDES[level]
            n = 0
            for kw in info["new_keywords"]:
                kw = kw.strip()
                if not kw or kw in existing:
                    continue
                conn.execute(
                    "INSERT INTO high_risk_signals(level, keyword, guide, source) VALUES(?,?,?,?)",
                    (level, kw, guide, "WS/T 591-2018 急诊预检分诊（第二批扩充）"),
                )
                existing.add(kw)
                n += 1
            stats[level] = n

        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM high_risk_signals").fetchone()[0]
        print(f"导入前 high_risk_signals: {before} 条")
        print(f"导入后 high_risk_signals: {after} 条")
        print(f"本次新增: {after - before} 条")
        for lv, n in stats.items():
            print(f"  {lv}: +{n}")
    finally:
        conn.close()
    return stats


if __name__ == "__main__":
    import_high_risk()
    print("红旗信号扩充完成。")
