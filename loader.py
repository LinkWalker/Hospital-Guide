"""知识数据加载器：优先从 SQLite 数据库读，数据库未初始化时回退 JSON 文件。

- 数据库已建（data/triage.db）→ 读数据库（线上真实数据源）
- 数据库未建 → 读 app/knowledge/*.json（开发兜底）
"""
import json
import os
from typing import List

_KNOWLEDGE_DIR = os.path.dirname(os.path.abspath(__file__))
_DOCS_DIR = os.path.join(_KNOWLEDGE_DIR, "medical_docs")


# ------------------ 底层：读原始 JSON 文件（供 seed 灌库用） ------------------
def read_departments_json() -> List[dict]:
    with open(os.path.join(_KNOWLEDGE_DIR, "departments.json"), encoding="utf-8") as f:
        return json.load(f)


def read_high_risk_json() -> dict:
    with open(os.path.join(_KNOWLEDGE_DIR, "high_risk.json"), encoding="utf-8") as f:
        return json.load(f)


def read_medical_docs_json() -> List[dict]:
    units = []
    if not os.path.isdir(_DOCS_DIR):
        return units
    for filename in sorted(os.listdir(_DOCS_DIR)):
        path = os.path.join(_DOCS_DIR, filename)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            content = f.read()
        for block in [b.strip() for b in content.split("\n\n") if b.strip()]:
            units.append({"text": block, "source": filename})
    return units


# ------------------ 上层：优先数据库，回退 JSON ------------------
def _db_ready() -> bool:
    from ..db.database import DB_PATH
    return os.path.exists(DB_PATH)


def load_departments() -> List[dict]:
    """科室规则（聚合后的规则列表）。优先数据库，回退 JSON。

    数据库版：按 recommended_dept 聚合 symptom_rules 的关键词。
    """
    if _db_ready():
        from ..db.database import query_all
        rows = query_all(
            "SELECT keyword, body_part, recommended_dept, alt_depts, priority, reason FROM symptom_rules"
        )
        if rows:
            return [{"symptom_keywords": [r["keyword"]],
                     "body_part": r["body_part"],
                     "recommended_dept": r["recommended_dept"],
                     "alt_depts": json.loads(r["alt_depts"] or "[]"),
                     "priority": r["priority"], "reason": r["reason"]}
                    for r in rows]
    return read_departments_json()


def load_high_risk() -> dict:
    """高危词库 {level: {label, guide, keywords}}。优先数据库，回退 JSON。"""
    if _db_ready():
        from ..db.database import query_all
        rows = query_all("SELECT level, keyword, guide FROM high_risk_signals")
        if rows:
            out = {"level": {}}
            for r in rows:
                lv = r["level"]
                if lv not in out["level"]:
                    out["level"][lv] = {
                        "label": "紧急" if lv == "emergency" else "次紧急",
                        "guide": r["guide"],
                        "keywords": [],
                    }
                out["level"][lv]["keywords"].append(r["keyword"])
            return out
    return read_high_risk_json()


def load_medical_docs() -> List[dict]:
    """医学知识文档。优先数据库 knowledge_docs 表，回退 JSON 切片。"""
    if _db_ready():
        from ..db.database import query_all
        rows = query_all("SELECT content, source FROM knowledge_docs")
        if rows:
            return [{"text": r["content"], "source": r["source"] or "db"} for r in rows]
    return read_medical_docs_json()


def load_department_names() -> List[str]:
    """所有科室名白名单（供 LLM 输出枚举校验）。优先 departments 表。"""
    if _db_ready():
        from ..db.database import query_all
        rows = query_all("SELECT name FROM departments ORDER BY name")
        if rows:
            return [r["name"] for r in rows]
    names = set()
    for r in read_departments_json():
        names.add(r["recommended_dept"])
        names.update(r.get("alt_depts") or [])
    return sorted(names)
