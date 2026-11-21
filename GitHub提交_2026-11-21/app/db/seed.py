"""种子数据：把现有 JSON 规则与知识文档灌入 SQLite，并初始化医院渠道表。

幂等：已存在则跳过。后续团队从官方数据源（CT08.000002 / WS-T 591 / CMeKG / 114）
补充数据时，直接往对应表 INSERT 即可。
"""
import json
import os

from .database import get_connection, DB_PATH
from ..knowledge.loader import read_departments_json as load_departments, read_high_risk_json as load_high_risk, read_medical_docs_json as load_medical_docs


# 科室白名单初始（category 大类，来源 CT08.00.00.0002 标准名，code 待补）
_DEPT_CATEGORY = {
    "心血管内科": "内科", "呼吸内科": "内科", "消化内科": "内科", "神经内科": "内科",
    "内分泌科": "内科", "肾内科": "内科", "血液科": "内科", "感染科": "内科",
    "风湿免疫科": "内科", "变态反应科": "内科", "普通内科": "内科",
    "骨科": "外科", "泌尿外科": "外科", "普通外科": "外科", "肛肠科": "外科",
    "乳腺外科": "外科",
    "皮肤科": "五官皮肤", "眼科": "五官皮肤", "耳鼻喉科": "五官皮肤", "口腔科": "五官皮肤",
    "妇科": "妇儿", "儿科": "妇儿",
    "精神心理科": "其他", "疼痛科": "其他",
    "急诊科": "急诊", "康复科": "其他", "全科": "全科", "综合门诊": "全科",
}

# 福州地区医院与挂号渠道初始数据。
# 坐标：GCJ-02 近似值，仅用于课程演示的“就近排序”；上线前必须以医院官方地址/地图校准。
_HOSPITALS = [
    {"name": "福建医科大学附属协和医院", "level": "三甲", "city": "福州",
     "address": "福州市鼓楼区新权路29号", "latitude": 26.0748, "longitude": 119.3009,
     "registration_channel": "114预约挂号平台/医院公众号/医院官网", "registration_url": "", "source": "官方渠道"},
    {"name": "福建医科大学附属第一医院", "level": "三甲", "city": "福州",
     "address": "福州市台江区茶亭中路20号", "latitude": 26.0663, "longitude": 119.2962,
     "registration_channel": "114预约挂号平台/医院公众号", "registration_url": "", "source": "官方渠道"},
    {"name": "福建省立医院", "level": "三甲", "city": "福州",
     "address": "福州市鼓楼区东街134号", "latitude": 26.0813, "longitude": 119.2986,
     "registration_channel": "114预约挂号平台/医院公众号", "registration_url": "", "source": "官方渠道"},
    {"name": "福州市第一医院", "level": "三甲", "city": "福州",
     "address": "福州市台江区达道路190号", "latitude": 26.0575, "longitude": 119.3074,
     "registration_channel": "114预约挂号平台/医院公众号", "registration_url": "", "source": "官方渠道"},
    {"name": "福建省人民医院", "level": "三甲", "city": "福州",
     "address": "福州市台江区八一七中路602号", "latitude": 26.0587, "longitude": 119.2926,
     "registration_channel": "114预约挂号平台/医院公众号", "registration_url": "", "source": "官方渠道"},
    {"name": "福建省妇幼保健院", "level": "三甲", "city": "福州",
     "address": "福州市鼓楼区道山路18号", "latitude": 26.0784, "longitude": 119.2952,
     "registration_channel": "医院公众号/医院官网", "registration_url": "", "source": "官方渠道"},
    {"name": "福建医科大学附属协和医院(旗山院区)", "level": "三甲", "city": "福州",
     "address": "福州市闽侯县上街镇学府南路6号", "latitude": 26.047071, "longitude": 119.19115,
     "registration_channel": "114预约挂号平台/医院公众号", "registration_url": "", "source": "高德 POI（演示坐标）"},
    {"name": "福建中医药大学附属第三人民医院", "level": "三甲", "city": "福州",
     "address": "福州市闽侯县上街镇国宾大道363号", "latitude": 26.075751, "longitude": 119.185216,
     "registration_channel": "114预约挂号平台/医院公众号", "registration_url": "", "source": "高德 POI（演示坐标）"},
    {"name": "中国人民解放军联勤保障部队第九〇〇医院仓山院区", "level": "三甲", "city": "福州",
     "address": "福州市仓山区洪山桥郭厝里90号", "latitude": 26.073252, "longitude": 119.246017,
     "registration_channel": "医院公众号", "registration_url": "", "source": "高德 POI（演示坐标）"},
    {"name": "福州大学附属省立医院(金山院区)", "level": "三甲", "city": "福州",
     "address": "福州市仓山区金山街道金榕南路516号", "latitude": 26.039761, "longitude": 119.27061,
     "registration_channel": "114预约挂号平台/医院公众号", "registration_url": "", "source": "高德 POI（演示坐标）"},
]


def _has_rows(conn, table: str) -> bool:
    return conn.execute(f"SELECT COUNT(1) FROM {table}").fetchone()[0] > 0


def seed_departments(conn):
    """科室白名单：从科室规则库提取所有出现过的科室名。"""
    if _has_rows(conn, "departments"):
        return
    names = set()
    for r in load_departments():
        names.add(r["recommended_dept"])
        names.update(r.get("alt_depts") or [])
    for name in sorted(names):
        conn.execute(
            "INSERT OR IGNORE INTO departments(name, category, source) VALUES(?,?,?)",
            (name, _DEPT_CATEGORY.get(name, "其他"), "CT08.00.00.0002 标准科室名（初始种子）"),
        )


def seed_symptom_rules(conn):
    """症状-科室映射：从现有 departments.json 迁移。"""
    if _has_rows(conn, "symptom_rules"):
        return
    for r in load_departments():
        for kw in r["symptom_keywords"]:
            conn.execute(
                "INSERT INTO symptom_rules(keyword, body_part, recommended_dept, alt_depts, priority, reason, source)"
                " VALUES(?,?,?,?,?,?,?)",
                (kw, r["body_part"], r["recommended_dept"],
                 json.dumps(r.get("alt_depts") or [], ensure_ascii=False),
                 r.get("priority", 50), r.get("reason"), r.get("source", "人工整理（初始种子）")),
            )


def seed_high_risk(conn):
    """急症红旗信号：从 high_risk.json 迁移。"""
    if _has_rows(conn, "high_risk_signals"):
        return
    data = load_high_risk()
    for level, info in data["level"].items():
        for kw in info["keywords"]:
            conn.execute(
                "INSERT INTO high_risk_signals(level, keyword, guide, source) VALUES(?,?,?,?)",
                (level, kw, info["guide"], "WS/T 591—2018 急诊预检分级（初始种子）"),
            )


def seed_hospitals(conn):
    """医院挂号渠道：初始示例 + 旧库坐标回填。"""
    for h in _HOSPITALS:
        conn.execute(
            "INSERT OR IGNORE INTO hospitals"
            "(name, level, city, address, latitude, longitude, registration_channel, registration_url, source)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (
                h["name"], h["level"], h["city"], h.get("address", ""),
                h.get("latitude"), h.get("longitude"),
                h["registration_channel"], h["registration_url"], h["source"],
            ),
        )
    # 旧库可能已经灌过部分医院但没有坐标：只补空值，不覆盖人工修改
    for h in _HOSPITALS:
        conn.execute(
            "UPDATE hospitals SET latitude=?, longitude=?,"
            " address=COALESCE(NULLIF(address,''), ?)"
            " WHERE name=? AND (latitude IS NULL OR longitude IS NULL)",
            (h.get("latitude"), h.get("longitude"), h.get("address", ""), h["name"]),
        )


def seed_knowledge_docs(conn):
    """医学知识文档：从 symptoms_guide.txt 迁移。"""
    if _has_rows(conn, "knowledge_docs"):
        return
    for u in load_medical_docs():
        conn.execute(
            "INSERT INTO knowledge_docs(title, content, source) VALUES(?,?,?)",
            (None, u["text"], u["source"]),
        )


def seed_all():
    """灌全部种子数据。"""
    if not os.path.exists(DB_PATH):
        raise RuntimeError("数据库文件不存在，请先 init_db() 建表")
    conn = get_connection()
    try:
        seed_departments(conn)
        seed_symptom_rules(conn)
        seed_high_risk(conn)
        seed_hospitals(conn)
        seed_knowledge_docs(conn)
        conn.commit()
    finally:
        conn.close()
