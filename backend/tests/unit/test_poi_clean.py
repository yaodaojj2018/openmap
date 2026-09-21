"""POI 清洗管道单测（纯函数）：类目过滤、uid/名称聚类去重、距离回填与排序。"""

from app.mapapi.provider import BD09Point
from app.models.poi import PoiRecord
from app.poi.service import CategorySpec, clean_records

CENTER: BD09Point = (116.316628, 39.981909)
SPEC = CategorySpec(key="medical", label="医疗（药店）", query="药店", filter_pattern=r"药")


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
