"""信息抽取模块测试。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.modules.nlu import build_extractor


def test_extract_part_and_nature():
    extractor = build_extractor()
    info = extractor.extract("我胸口闷，还有点喘不上气，已经三天了")
    assert info.part == "胸部"
    assert info.nature == "闷"
    assert info.duration == "三天"


def test_extract_accompanying():
    extractor = build_extractor()
    info = extractor.extract("肚子疼还恶心呕吐")
    assert info.part == "腹部"
    assert "恶心" in info.accompanying


def test_extract_population():
    extractor = build_extractor()
    info = extractor.extract("我家小孩咳嗽发烧两天了")
    assert info.population in ("儿童", "孩子", "小孩")
