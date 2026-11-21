"""急症拦截模块（安全关键，纯代码，不依赖大模型）。

逻辑：用高危词库 + 关键词/组合规则匹配用户原始文本。
- 命中 emergency 级：直接判定紧急，返回紧急引导。
- 命中 urgent 级：判定次紧急，建议尽快就医。
- 未命中：返回 None，正常走分诊流程。

设计原则：宁可多拦不可漏拦。此模块全量单测覆盖高危词。
"""
from ..knowledge.loader import load_high_risk


class TriageInterceptor:
    """急症拦截规则引擎。"""

    def __init__(self):
        raw = load_high_risk()
        self.levels = raw["level"]
        # 预编译匹配表：level -> keywords
        self._keyword_map = {
            level: info["keywords"] for level, info in self.levels.items()
        }
        # 口语里“胸痛 + 冷汗/喘不上气”不一定连成一个词，需要组合规则兜底。
        self._emergency_combos = [
            (("胸痛", "胸闷", "心口痛"), ("冷汗", "大汗", "出冷汗", "冒冷汗")),
            (("胸痛", "胸闷"), ("呼吸困难", "喘不上气", "气短", "窒息")),
        ]

    def intercept(self, text: str) -> dict | None:
        """对用户原始文本做急症拦截。

        返回命中信息 dict（含 level / label / guide），未命中返回 None。
        同时命中多级时，以更紧急的 emergency 为准。
        """
        for primary, secondary in self._emergency_combos:
            if any(k in text for k in primary) and any(k in text for k in secondary):
                info = self.levels["emergency"]
                return {
                    "level": "emergency",
                    "label": info["label"],
                    "guide": info["guide"],
                    "keyword": "胸痛 + 冷汗/气短（组合）",
                }
        for level in ("emergency", "urgent"):
            for kw in self._keyword_map[level]:
                if kw in text:
                    info = self.levels[level]
                    return {
                        "level": level,
                        "label": info["label"],
                        "guide": info["guide"],
                        "keyword": kw,
                    }
        return None


def build_interceptor() -> TriageInterceptor:
    return TriageInterceptor()
