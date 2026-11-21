# -*- coding: utf-8 -*-
"""一次性验证脚本：抽查新增急症词 / 误伤检查 / 新规则推荐。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules.triage import build_interceptor
from app.modules.rag import build_retriever
from app.schemas.models import SymptomInfo

it = build_interceptor()
print("== 新增急症词抽查 ==")
for t in ["我心跳停止了", "孩子溺水了", "医生说我心肌梗死", "怀疑脑梗", "腿骨折了",
          "拉黑便", "被车撞了", "一直喘不过气", "想割腕", "发烧抽搐"]:
    hit = it.intercept(t)
    print(f"{t!r:16} -> {hit['level'] if hit else None} ({hit['keyword'] if hit else ''})")

print()
print("== 正常症状不误伤 ==")
for t in ["我肚子有点疼，想吃点清淡的", "我胸口闷痛，喘不上气，三天了", "我有点头痛想休息"]:
    hit = it.intercept(t)
    print(f"{t!r:16} -> {hit['level'] if hit else '未拦截'}")

print()
print("== 新规则抽查 ==")
r = build_retriever()
for t in ["我长痔疮了", "宝宝发烧两天", "我最近贫血头晕", "腰酸还腿肿", "我焦虑睡不好",
          "脖子上有个肿块", "手指被门夹了有点疼", "我痛经月经不调"]:
    hit = r.rule.search(SymptomInfo(), raw_text=t)
    print(f"{t!r:16} -> {hit['recommended_dept'] if hit else '未命中'}")
