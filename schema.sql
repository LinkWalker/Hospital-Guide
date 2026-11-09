-- =============================================================
-- 智能导诊与挂号导航助手 · 数据库 schema（SQLite）
-- 数据来源对应团队提供的数据源清单：
--   1. 科室名称白名单  → 《医疗卫生机构业务科室分类与代码》CT08.00.000002
--   2. 急症与红旗信号  → 《急诊预检分诊》WS/T 591—2018（I级/II级急危重症）
--   3. 症状-科室规则   → CMeKG 中文医疗知识图谱 / 中文患者主诉-专科数据集
--   4. 医院与挂号渠道   → 三甲医院官网 / 114 预约挂号平台（福州地区）
-- 说明：开发期用 SQLite，上线可迁移 PostgreSQL/MySQL（SQL 标准语法兼容）。
-- =============================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------
-- 1. 科室白名单（标准科室表）
--    source 标注数据来源：CT08.00.00.0002 标准科室代码，后续需补全 code
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS departments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT,                 -- 标准科室代码（CT08.00.00.0002，待补）
    name          TEXT NOT NULL UNIQUE, -- 标准科室名（枚举校验用，唯一）
    category      TEXT,                 -- 大类：内科 / 外科 / 五官 / 妇儿 / 其他
    description   TEXT,                 -- 科室简介
    source        TEXT DEFAULT '人工整理',  -- 数据来源
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------
-- 2. 症状-科室映射规则表（核心分诊规则）
--    recommended_dept 引用 departments.name，保证科室名来自白名单（防幻觉）
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS symptom_rules (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword            TEXT NOT NULL,      -- 触发关键词（如 胸痛/胸闷）
    body_part          TEXT,               -- 部位
    recommended_dept   TEXT NOT NULL,      -- 主推科室（须在 departments.name 内）
    alt_depts          TEXT DEFAULT '[]',  -- 备选科室（JSON 数组字符串）
    priority           INTEGER DEFAULT 50, -- 匹配优先级（越大越优先）
    reason             TEXT,               -- 判断依据
    source             TEXT DEFAULT '人工整理',  -- 来源：CMeKG / 主诉数据集 / 人工
    created_at         TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (recommended_dept) REFERENCES departments(name)
);

-- ---------------------------------------------------------------
-- 3. 急症红旗信号表（安全关键，纯代码匹配）
--    level: emergency(I级) / urgent(II级)，对应 WS/T 591—2018
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS high_risk_signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    level       TEXT NOT NULL,         -- emergency(I级) / urgent(II级)
    keyword     TEXT NOT NULL,         -- 高危关键词
    guide       TEXT,                  -- 命中后的引导文案
    source      TEXT DEFAULT 'WS/T 591—2018',
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------
-- 4. 医院与挂号渠道表（福州地区）
--    数据来源：三甲医院官网 / 官方公众号 / 114 预约挂号平台
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hospitals (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL UNIQUE, -- 医院名
    level                TEXT,                 -- 等级（三甲/三乙...）
    city                 TEXT DEFAULT '福州',
    registration_channel TEXT,                 -- 挂号渠道：114平台/公众号/官网/窗口
    registration_url     TEXT,                 -- 挂号入口
    source               TEXT DEFAULT '官方渠道',
    created_at           TEXT DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------
-- 5. 医学知识文档表（供向量/关键词检索作为生成依据）
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_docs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT,
    content     TEXT NOT NULL,
    source      TEXT,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

-- 常用查询索引
CREATE INDEX IF NOT EXISTS idx_symptom_rules_keyword ON symptom_rules(keyword);
CREATE INDEX IF NOT EXISTS idx_high_risk_level ON high_risk_signals(level);
CREATE INDEX IF NOT EXISTS idx_departments_category ON departments(category);
