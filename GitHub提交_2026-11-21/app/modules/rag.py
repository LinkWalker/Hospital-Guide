"""知识检索模块（RAG）：规则库检索 + 内置轻量向量检索。

- 规则库检索（主路径）：按症状关键词匹配科室规则，给出主推 + 备选 + 依据。
- 向量检索（可选增强）：对医学知识文档做简单词频向量 + 余弦相似度检索，
  作为生成阶段的证据。为免重依赖，内置纯 Python 实现；接真实 Embedding/向量库
  时可替换 VectorRetriever 的实现而不影响上层。

科室名集合由规则库导出，用于 LLM 输出的枚举校验。
"""
import math
import re
from collections import Counter
from typing import List

from ..schemas.models import RetrievedDoc, SymptomInfo
from ..knowledge.loader import load_departments, load_medical_docs

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+")


def _tokenize(text: str) -> List[str]:
    """极简中文分词：按连续中文/英文数字切块（够用于检索，无需 jieba）。"""
    return [t for t in _TOKEN_RE.findall(text) if t]


class RuleRetriever:
    """科室规则库检索器。"""

    def __init__(self):
        self.rules = load_departments()

    @property
    def department_names(self) -> List[str]:
        """科室名白名单（供 LLM 输出枚举校验）。优先 departments 表，回退规则聚合。"""
        from ..knowledge.loader import load_department_names
        names = load_department_names()
        if names:
            return names
        names_set = set()
        for r in self.rules:
            names_set.add(r["recommended_dept"])
            names_set.update(r.get("alt_depts") or [])
        return sorted(names_set)

    def search(self, symptom: SymptomInfo, raw_text: str | None = None) -> dict | None:
        """按症状匹配规则。返回命中规则 dict，未命中返回 None。

        匹配依据：优先用用户原始文本（能命中"胸口闷"等连续词），
        其次用结构化文本兜底。命中多条取 priority 最高者。
        """
        candidates = []
        if raw_text:
            candidates.append(raw_text)
        candidates.append(self._build_text(symptom))
        best = None
        for rule in self.rules:
            matched = any(kw in text for text in candidates for kw in rule["symptom_keywords"])
            if matched and (best is None or rule["priority"] > best["priority"]):
                best = rule
        return best

    @staticmethod
    def _build_text(s: SymptomInfo) -> str:
        parts = []
        if s.part:
            parts.append(s.part)
        if s.nature:
            parts.append(s.nature)
        parts += s.accompanying
        return " ".join(parts)


class VectorRetriever:
    """内置轻量向量检索：词频向量 + 余弦相似度。可被外部向量库实现替换。"""

    def __init__(self):
        self.units = load_medical_docs()
        # 预计算每段文档的词频向量
        self._vecs = [self._tf(unit["text"]) for unit in self.units]

    @staticmethod
    def _tf(text: str) -> Counter:
        return Counter(_tokenize(text))

    @staticmethod
    def _cos(a: Counter, b: Counter) -> float:
        """余弦相似度。"""
        common = set(a) & set(b)
        if not common:
            return 0.0
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    def search(self, symptom: SymptomInfo, top_k: int = 5) -> List[RetrievedDoc]:
        query = " ".join(_tokenize(self._query_text(symptom)))
        qvec = self._tf(query)
        scored = []
        for idx, unit in enumerate(self.units):
            s = self._cos(qvec, self._vecs[idx])
            if s > 0:
                scored.append((s, unit))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            RetrievedDoc(text=unit["text"], source=unit["source"], score=round(score, 4))
            for score, unit in scored[:top_k]
        ]

    @staticmethod
    def _query_text(s: SymptomInfo) -> str:
        parts = [s.part or "", s.nature or ""]
        parts += s.accompanying
        return " ".join(parts)


class KnowledgeRetriever:
    """对外检索入口：规则库（主）+ 向量检索（增强证据）。"""

    def __init__(self, use_vector: bool = True):
        self.rule = RuleRetriever()
        self.vector = VectorRetriever() if use_vector else None
        self.department_names = self.rule.department_names

    def retrieve(self, symptom: SymptomInfo, raw_text: str | None = None) -> dict:
        """返回 { rule_hit, evidence }。"""
        rule_hit = self.rule.search(symptom, raw_text)
        evidence: List[RetrievedDoc] = []
        if self.vector:
            evidence = self.vector.search(symptom)
        return {"rule_hit": rule_hit, "evidence": evidence}


def build_retriever(use_vector: bool = True) -> KnowledgeRetriever:
    return KnowledgeRetriever(use_vector=use_vector)
