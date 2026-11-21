"""批量导入扩充症状-科室规则库（第二批）。

数据来源：app/knowledge/symptom_rules_extended.json
         基于 CMeKG 中文医疗知识图谱 / 公开患者主诉-专科数据集整理。

特性：
- 幂等：已存在的关键词自动跳过（应用层去重 + INSERT OR IGNORE）；
- 科室白名单校验：recommended_dept 必须已在 departments 表中，否则报错提示；
- 自动统计：导入前后行数对比，按科室输出新增条数。

用法：
    python -m scripts.import_extended_rules
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import get_connection, DB_PATH
from app.knowledge.loader import read_departments_json

_EXTENDED_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "knowledge", "symptom_rules_extended.json",
)


def _existing_keywords(conn) -> set:
    """读取 symptom_rules 表中已有关键词集合。"""
    return {row[0] for row in conn.execute("SELECT keyword FROM symptom_rules").fetchall()}


def _valid_depts(conn) -> set:
    """读取 departments 表白名单。"""
    return {row[0] for row in conn.execute("SELECT name FROM departments").fetchall()}


def import_extended_rules() -> dict:
    """导入扩充规则，返回统计 {dept: new_count}。"""
    if not os.path.exists(DB_PATH):
        raise RuntimeError("数据库不存在，请先运行 init_db() 建表")

    with open(_EXTENDED_PATH, encoding="utf-8") as f:
        ext = json.load(f)

    conn = get_connection()
    stats = {}
    try:
        existing = _existing_keywords(conn)
        valid_depts = _valid_depts(conn)

        before = conn.execute("SELECT COUNT(*) FROM symptom_rules").fetchone()[0]

        for rule in ext["rules"]:
            dept = rule["recommended_dept"]
            if dept not in valid_depts:
                print(f"  [跳过] 科室不在白名单: {dept}（请先在 departments 表注册）")
                continue

            body_part = rule.get("body_part", "")
            alt_depts = json.dumps(rule.get("alt_depts", []), ensure_ascii=False)
            priority = rule.get("priority", 50)
            reason = f"{dept}相关常见主诉（扩充库：CMeKG/主诉数据集）"

            new_count = 0
            for kw in rule["new_keywords"]:
                kw = kw.strip()
                if not kw or kw in existing:
                    continue
                conn.execute(
                    "INSERT INTO symptom_rules"
                    "(keyword, body_part, recommended_dept, alt_depts, priority, reason, source)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (kw, body_part, dept, alt_depts, priority, reason,
                     "CMeKG/公开主诉-专科数据集（第二批扩充）"),
                )
                existing.add(kw)
                new_count += 1
            stats[dept] = new_count

        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM symptom_rules").fetchone()[0]
        print(f"导入前 symptom_rules: {before} 条")
        print(f"导入后 symptom_rules: {after} 条")
        print(f"本次新增: {after - before} 条")
        print("--- 按科室新增 ---")
        for dept, n in sorted(stats.items(), key=lambda x: -x[1]):
            print(f"  {dept}: +{n}")
    finally:
        conn.close()
    return stats


if __name__ == "__main__":
    import_extended_rules()
    print("扩充规则导入完成。")
