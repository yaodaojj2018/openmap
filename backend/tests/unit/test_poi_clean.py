"""POI 清洗管道单测（纯函数）：类目过滤、uid/名称聚类去重、附属坍缩、距离回填与排序。"""

import pytest

from app.mapapi.provider import BD09Point
from app.models.poi import PoiRecord
from app.poi.service import CategorySpec, clean_records

CENTER: BD09Point = (116.316628, 39.981909)
SPEC = CategorySpec(key="medical", label="医疗（药店）", queries=("药店",), filter_pattern=r"药")
EDU_SPEC = CategorySpec(
    key="education",
    label="教育（小学）",
    queries=("小学", "学校"),
    filter_pattern=r"小学|九年一贯制",
)


def poi(uid: str, name: str, d_lng: float, d_lat: float, tag: str = "药店") -> PoiRecord:
    return PoiRecord(uid=uid, name=name, lng=CENTER[0] + d_lng, lat=CENTER[1] + d_lat, tag=tag)


def test_noise_filtered_by_pattern() -> None:
    # 名称与标签都不含"药"的条目被剔除（类目噪音）
    records = [poi("a", "某某中学", 0.001, 0.001, tag="中学"), poi("b", "社区药店", 0.001, 0.001)]
    cleaned = clean_records(records, SPEC, CENTER, dedup_distance_m=30.0)
    assert [r.uid for r in cleaned] == ["b"]


def test_dedup_same_uid_and_same_name_nearby() -> None:
    records = [
        poi("a", "金象大药房", 0.001, 0.001),
        poi("a", "金象大药房", 0.0012, 0.0012),  # 同 uid 重复
        poi("c", "金象大药房", 0.0011, 0.0011),  # 同名 30m 内（连锁重复）
        poi("d", "金象大药房", 0.008, 0.008),  # 同名但 ~800m 外（分店，保留）
    ]
    cleaned = clean_records(records, SPEC, CENTER, dedup_distance_m=30.0)
    assert [r.uid for r in cleaned] == ["a", "d"]


def test_category_and_distance_backfilled_sorted() -> None:
    near = poi("near", "近药店", 0.001, 0.0)  # ~85m
    far = poi("far", "远药店", 0.005, 0.0)  # ~427m
    cleaned = clean_records([far, near], SPEC, CENTER, dedup_distance_m=30.0)
    assert all(r.category == "medical" for r in cleaned)
    assert [r.uid for r in cleaned] == ["near", "far"]  # 按距离升序
    assert cleaned[0].distance_m is not None and cleaned[0].distance_m < 120
    assert cleaned[1].distance_m is not None and cleaned[1].distance_m > 350


# ---- 附属 POI 坍缩与教育召回口径 ----


def edu(uid: str, name: str, d_lng: float, d_lat: float, tag: str) -> PoiRecord:
    return poi(uid, name, d_lng, d_lat, tag=tag)


@pytest.mark.regression
def test_gate_only_school_kept_as_representative() -> None:
    """主体缺席时门禁 POI 保留为学校代表。

    回归（BF-010）：百度 v3 周边检索分词索引对"实验小学"复合词不命中，
    安吉路新文实验小学（杭州和宁文华府北门正对）query=小学/实验小学 均不召回
    主体，仅"学校"能召回其门禁 POI——附属坍缩必须保留该唯一代表，且多门只留最近。
    """
    records = [
        edu("g1", "杭州市安吉路新文实验小学西门", 0.0015, 0.0015, tag="出入口;门"),  # ~211m
        edu("g2", "杭州市安吉路新文实验小学北门", 0.0025, 0.0020, tag="出入口;门"),  # ~308m
    ]
    cleaned = clean_records(records, EDU_SPEC, CENTER, dedup_distance_m=30.0)
    assert [r.uid for r in cleaned] == ["g1"]  # 多门只留最近一个


@pytest.mark.regression
def test_gate_collapsed_when_main_present() -> None:
    """主体在场时同校门禁剔除——同校不得重复计数（docs/01 §5.3 数量虚高）。

    回归（BF-010）：教育补"学校"检索词后，普通小学会同时召回主体与门禁
    （如"杭州市长阳小学"+“-南门”），无坍缩则同校计 2~3 次，充足度虚高。
    """
    records = [
        edu("main", "杭州市长阳小学", 0.008, 0.008, tag="教育培训;小学"),
        edu("s-gate", "杭州市长阳小学-南门", 0.0079, 0.0078, tag="出入口;门"),
    ]
    cleaned = clean_records(records, EDU_SPEC, CENTER, dedup_distance_m=30.0)
    assert [r.uid for r in cleaned] == ["main"]


@pytest.mark.regression
def test_nine_year_school_kept_by_tag_and_middle_school_filtered() -> None:
    """九年一贯制学校含小学部（tag 命中保留）；纯中学是类目噪音（剔除）。

    回归（BF-010）：query=小学 的窄召回漏掉九年一贯制学校（实测和宁文华府
    922m 的绿城育华亲亲学校缺席）；白名单扩 tag"九年一贯制"补齐，中学不误入。
    """
    records = [
        edu("nine", "杭州绿城育华亲亲学校", 0.006, -0.006, tag="教育培训;九年一贯制学校"),
        edu("mid", "浙江师范大学附属星澜中学", 0.004, 0.004, tag="教育培训;中学;初中"),
        edu("bld", "绿城育华亲亲学校教学楼-3幢", 0.0061, -0.0061, tag="教育培训;校内设施;教学楼"),
    ]
    cleaned = clean_records(records, EDU_SPEC, CENTER, dedup_distance_m=30.0)
    assert [r.uid for r in cleaned] == ["nine"]
