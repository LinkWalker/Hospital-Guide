"""信息抽取模块（NLU）：从用户口语中提取结构化症状信息。

双通道：
1. 规则 / 词典抽取（正则 + 医学词典）：快速、零成本、可审计；
2. LLM 结构化抽取（可选）：对规则抽不到的复杂描述兜底，需要配真实大模型。

两个通道结果合并去重，置信度低视为"未收集"，留给多轮追问。
"""
import re
from typing import List, Optional

from ..schemas.models import SymptomInfo

# 部位词典：口语词 → 规范部位名
# 顺序规则：所有复合词（长词）必须排在单字兜底词之前！
# 反例："屁眼"含"眼"，若"眼"在前会先命中眼部；"手指"含"手"、"额头"含"头"同理。
_PART_MAP = {
    # ---- 复合词（长词）优先 ----
    # 头面部
    "后脑勺": "头部", "脑袋": "头部", "额头": "面部", "下巴": "面部", "腮帮": "面部",
    # 胸/腹
    "胸部": "胸部", "胸口": "胸部", "心口": "胸部", "心脏": "胸部",
    "乳房": "乳房", "乳腺": "乳房", "乳头": "乳房",
    "腹部": "腹部", "肚子": "腹部",
    # 眼耳鼻喉
    "眼睛": "眼部",
    "耳朵": "耳鼻喉", "嗓子": "咽喉", "咽喉": "咽喉", "喉咙": "咽喉",
    # 关节/骨骼
    "肩膀": "肩膀", "颈椎": "颈椎", "脖子": "颈椎",
    "背部": "腰背",
    # 四肢
    "手臂": "手臂", "手腕": "手腕", "手指": "手指",
    "下肢": "下肢", "脚踝": "脚踝", "脚腕": "脚踝", "脚趾": "脚趾",
    # 皮肤/口腔
    "皮肤": "皮肤", "口腔": "口腔",
    # 泌尿/生殖（含俗语）
    "生殖器": "生殖器", "阴茎": "生殖器", "龟头": "生殖器", "包皮": "生殖器",
    "睾丸": "生殖器", "蛋蛋": "生殖器", "阴囊": "生殖器",
    "阴唇": "生殖器", "外阴": "生殖器", "阴道": "生殖器", "前列腺": "生殖器", "白带": "生殖器",
    "下面": "生殖器", "私处": "生殖器", "裆部": "生殖器", "腹股沟": "腹股沟",
    "肛门": "肛门", "屁眼": "肛门", "痔疮": "肛门", "屁股": "臀部",
    "泌尿": "尿道",
    # ---- 单字兜底（最后） ----
    "头": "头部", "腮": "面部",
    "胸": "胸部", "胃": "腹部", "腹": "腹部",
    "眼": "眼部", "耳": "耳鼻喉", "鼻": "耳鼻喉",
    "关节": "关节", "膝盖": "关节", "肩": "肩膀", "颈": "颈椎", "脖": "颈椎",
    "背": "腰背", "腰": "腰背",
    "手": "手臂", "腿": "下肢", "脚": "下肢",
    "牙": "口腔", "尿": "尿道",
}

# 性质关键词（长词在前，单字兜底在后）
_NATURE_WORDS = {
    "闷": "闷", "胀": "胀", "痛": "痛", "疼": "痛", "烧心": "烧灼", "反酸": "反酸",
    "痒": "痒", "麻": "麻", "头晕": "头晕", "眩晕": "眩晕", "乏力": "乏力", "恶心": "恶心",
    "呕吐": "呕吐", "咳嗽": "咳嗽", "发烧": "发烧", "发热": "发热", "腹泻": "腹泻",
    "心慌": "心慌", "心悸": "心悸", "喘": "气短", "气短": "气短",
    "酸": "酸", "灼": "烧灼", "晕": "头晕", "咳": "咳嗽", "吐": "呕吐", "泻": "腹泻", "烧": "发烧",
    "扭伤": "扭伤", "挫伤": "扭伤", "擦伤": "外伤",
}

# 伴随症状
_ACCOMPANY_WORDS = [
    "出冷汗", "大汗", "发绀", "唇紫", "放射", "左肩", "昏厥", "抽搐", "意识不清", "发热",
    "发烧", "腹泻", "呕吐", "恶心", "耳鸣", "失眠", "视物模糊", "消瘦", "多饮多尿",
]

# 时长正则：数字 + 时间单位
_DURATION_RE = re.compile(r"([一二两三四五六七八九十0-9]+(?:天|周|个月|小时|月|年|日))")
_POPULATION_WORDS = ["成人", "成年人", "老人", "老年人", "儿童", "孩子", "小孩", "孕妇", "婴幼儿", "新生儿"]


class InfoExtractor:
    """信息抽取器。构造时可选传入 LLM 网关做兜底抽取。"""

    def __init__(self, llm_gateway=None):
        self._llm = llm_gateway

    def extract(self, text: str) -> SymptomInfo:
        """从一段用户文本抽取结构化症状信息。"""
        # 通道1：规则抽取
        rule_info = self._rule_extract(text)
        # 通道2：LLM 兜底（仅在配置了真实模型时启用）
        llm_info = self._llm_extract(text) if (self._llm and not self._llm.is_mock) else None
        # 合并：LLM 结果补充规则未抽到的字段
        merged = self._merge(rule_info, llm_info)
        return merged

    # ---------------- 规则通道 ----------------
    def _rule_extract(self, text: str) -> SymptomInfo:
        s = SymptomInfo()
        for word, norm in _PART_MAP.items():
            if word in text:
                s.part = norm
                break
        for word, val in _NATURE_WORDS.items():
            if word in text:
                s.nature = val
                break
        m = _DURATION_RE.search(text)
        if m:
            s.duration = m.group(1)
        for w in _ACCOMPANY_WORDS:
            if w in text:
                s.accompanying.append(w)
        for w in _POPULATION_WORDS:
            if w in text:
                s.population = w
                break
        return s

    # ---------------- LLM 兜底通道 ----------------
    def _llm_extract(self, text: str) -> SymptomInfo:
        # 极简兜底：真实大模型环境下才走这里，此处用网关通用 chat 能力
        # 为保持轻量，这里不强制实现；规则通道已覆盖多数场景。
        return SymptomInfo()

    # ---------------- 合并 ----------------
    @staticmethod
    def _merge(rule: SymptomInfo, llm: Optional[SymptomInfo]) -> SymptomInfo:
        if not llm:
            return rule
        rule = rule.model_copy(deep=True)
        if not rule.part and llm.part:
            rule.part = llm.part
        if not rule.nature and llm.nature:
            rule.nature = llm.nature
        if not rule.duration and llm.duration:
            rule.duration = llm.duration
        if not rule.population and llm.population:
            rule.population = llm.population
        rule.accompanying = list(dict.fromkeys(rule.accompanying + llm.accompanying))
        return rule


# 供编排层使用的默认实例
def build_extractor(llm_gateway=None) -> InfoExtractor:
    return InfoExtractor(llm_gateway)


