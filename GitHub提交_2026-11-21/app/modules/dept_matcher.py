"""科室名归一化与模糊匹配。

背景：
- 规则库里的科室名是标准名（如「心血管内科」）；
- 用户/大模型经常返回口语或平台写法（「心内科」「心血管科」「耳鼻咽喉科」）；
- 直接做字符串相等校验会把可用结果误判为“编造科室”。

策略（从严格到宽松）：
1. 精确匹配标准名；
2. 别名表精确匹配；
3. 标准名包含匹配（处理“建议挂心血管内科”这类前后缀）；
4. difflib 相似度兜底（阈值默认 0.72，且长度差不超过 2）。

任何一步失败都返回 None，由调用方决定“拒绝/降级”，绝不把未知科室硬塞进结果。
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Iterable, Optional

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ALIAS_PATH = os.path.join(_APP_DIR, "knowledge", "department_aliases.json")

_DEFAULT_THRESHOLD = 0.72
_STRIP_RE = re.compile(r"[\s\-_/\\（）()【】\[\]，,。.;；:：、]+")
_PREFIX_RE = re.compile(r"^(建议|推荐|请挂|可挂|应该挂|可以挂|挂号|就诊|科室|科别)[:：]?")
_SUFFIX_RE = re.compile(r"(医院|门诊部)$")


@lru_cache(maxsize=1)
def load_alias_config() -> dict:
    """读取别名配置；文件缺失时返回空配置，不影响主流程。"""
    try:
        with open(_ALIAS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"threshold": _DEFAULT_THRESHOLD, "aliases": {}}
        aliases = data.get("aliases") or {}
        if not isinstance(aliases, dict):
            aliases = {}
        return {
            "threshold": float(data.get("threshold", _DEFAULT_THRESHOLD)),
            "aliases": {str(k): str(v) for k, v in aliases.items()},
        }
    except (OSError, ValueError, TypeError):
        return {"threshold": _DEFAULT_THRESHOLD, "aliases": {}}


def _normalize_text(name: str) -> str:
    """统一全半角、去空白/标点、去常见前后缀。"""
    text = unicodedata.normalize("NFKC", str(name or ""))
    text = _STRIP_RE.sub("", text)
    text = _PREFIX_RE.sub("", text)
    text = _SUFFIX_RE.sub("", text)
    return text.strip()


def _valid_canonical_set(department_names: Iterable[str]) -> list[str]:
    """去重并保持稳定顺序；过滤空值。"""
    seen = set()
    out = []
    for name in department_names or []:
        n = str(name or "").strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _alias_map(canonical: list[str]) -> dict[str, str]:
    """只保留 value 在标准名集合内的别名，防止配置把模型引到不存在的科室。"""
    cfg = load_alias_config()
    canonical_set = set(canonical)
    return {
        _normalize_text(alias): target
        for alias, target in cfg["aliases"].items()
        if target in canonical_set and _normalize_text(alias)
    }


def _best_fuzzy(text: str, candidates: Iterable[str], threshold: float) -> Optional[str]:
    """在候选标准名里找最相似的；约束长度差，避免短词乱配。"""
    if len(text) < 2:
        return None
    best_name = None
    best_score = 0.0
    for name in candidates:
        if abs(len(text) - len(name)) > 2:
            continue
        score = SequenceMatcher(None, text, name).ratio()
        if score > best_score:
            best_score = score
            best_name = name
    if best_name is not None and best_score >= threshold:
        return best_name
    return None


def match_department(
    raw_name: str,
    department_names: Iterable[str],
    threshold: Optional[float] = None,
) -> Optional[str]:
    """把任意科室写法归一化为标准科室名；无法可靠匹配时返回 None。

    这是 LLM 输出校验和前端展示共用的唯一入口。
    """
    canonical = _valid_canonical_set(department_names)
    if not canonical:
        return None

    text = _normalize_text(raw_name)
    if not text:
        return None

    # 1) 精确
    if text in canonical:
        return text

    # 2) 别名
    aliases = _alias_map(canonical)
    if text in aliases:
        return aliases[text]

    # 3) 标准名包含（处理“建议挂心血管内科”“心血管内科门诊”）
    contained = [name for name in canonical if name in text]
    if contained:
        # 取最长标准名，避免“内科”抢先匹配“心血管内科”
        return max(contained, key=len)

    # 4) 别名包含（处理“建议看心内科”）
    # 只允许 >=3 字的别名做“包含”匹配：否则“内科”“外科”这类通用短词
    # 会把“心血内科”这种漏字写法劫持成“普通内科”。
    alias_contained = [
        (alias, target) for alias, target in aliases.items()
        if len(alias) >= 3 and alias in text
    ]
    if alias_contained:
        alias_contained.sort(key=lambda pair: len(pair[0]), reverse=True)
        return alias_contained[0][1]

    # 5) 模糊兜底：标准名 + 别名 key 都作为候选
    cfg = load_alias_config()
    use_threshold = cfg["threshold"] if threshold is None else float(threshold)
    fuzzy_candidates = canonical + [a for a in aliases if len(a) >= 2]
    best = _best_fuzzy(text, fuzzy_candidates, use_threshold)
    if best in aliases:
        return aliases[best]
    return best


def explain_match(
    raw_name: str,
    department_names: Iterable[str],
    threshold: Optional[float] = None,
) -> dict:
    """返回匹配详情，供调试/测试/接口展示使用。"""
    canonical = _valid_canonical_set(department_names)
    text = _normalize_text(raw_name)
    matched = match_department(raw_name, canonical, threshold)
    return {
        "raw": raw_name,
        "normalized": text,
        "matched": matched,
        "is_exact": bool(text and text in canonical),
        "is_alias": bool(text and text in _alias_map(canonical)),
        "method": (
            "exact" if text in canonical
            else "alias" if text in _alias_map(canonical)
            else "contains" if matched
            else "rejected"
        ),
    }
