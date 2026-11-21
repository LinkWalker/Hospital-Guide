"""医院定位与就近推荐。

两种数据源：
1. local（默认）：hospitals 表中的福州医院 + Haversine 距离，离线可用；
2. amap（可选）：配置 AMAP_API_KEY 后调用高德“周边搜索”API，返回真实 POI。

坐标说明：
- 数据库中的坐标是 GCJ-02 近似值，仅用于课程演示；
- 浏览器 Geolocation 返回 WGS-84，两者在福州市区有几十到几百米偏移，
  对“按距离排序”的演示影响可接受；生产环境应先做坐标转换并用官方地址校准。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两点球面距离（公里）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def amap_marker_url(lat: float, lng: float, name: str) -> str:
    """打开高德地图标记页，不需要 JS SDK / Key。"""
    return (
        "https://uri.amap.com/marker"
        f"?position={lng:.6f},{lat:.6f}"
        f"&name={quote(str(name or '医院'))}"
        "&coordinate=gaode&callnative=1&src=menzhen-paidui"
    )


def _row_to_item(row: Dict[str, Any], origin: Optional[tuple] = None) -> Optional[Dict[str, Any]]:
    try:
        lat = float(row.get("latitude"))
        lng = float(row.get("longitude"))
    except (TypeError, ValueError):
        return None
    distance = None
    if origin:
        distance = round(haversine_km(origin[0], origin[1], lat, lng), 2)
    return {
        "name": row.get("name") or "未知医院",
        "level": row.get("level") or "",
        "address": row.get("address") or "",
        "latitude": lat,
        "longitude": lng,
        "distance_km": distance,
        "registration_channel": row.get("registration_channel") or "",
        "registration_url": row.get("registration_url") or "",
        "map_url": amap_marker_url(lat, lng, row.get("name") or "医院"),
        "source": "local",
    }


def nearby_from_rows(rows: Iterable[Dict[str, Any]], lat: float, lng: float, limit: int = 6) -> List[Dict[str, Any]]:
    items = []
    for row in rows:
        item = _row_to_item(row, origin=(lat, lng))
        if item:
            items.append(item)
    items.sort(key=lambda x: (x["distance_km"] is None, x["distance_km"] or 0))
    return items[: max(1, min(int(limit), 20))]


def amap_input_tip(address: str, api_key: str, city: str = "福州", timeout: float = 8.0) -> Dict[str, Any]:
    """把用户输入的地点（如“旗山校区”）解析成高德坐标。

    用输入提示 API 而不是让大模型直接编经纬度：位置必须落到真实 POI/地址上。
    """
    import httpx

    params = {
        "key": api_key,
        "keywords": address,
        "city": city,
        "citylimit": "true",
        "datatype": "all",
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.get("https://restapi.amap.com/v3/assistant/inputtips", params=params)
        resp.raise_for_status()
        data = resp.json()
    if str(data.get("status")) != "1":
        raise RuntimeError(f"高德输入提示失败：{data.get('info') or data.get('infocode')}")

    for tip in data.get("tips") or []:
        loc = str(tip.get("location") or "")
        if "," not in loc:
            continue
        lng_s, lat_s = loc.split(",", 1)
        try:
            lng, lat = float(lng_s), float(lat_s)
        except ValueError:
            continue
        district = tip.get("district")
        if isinstance(district, list):
            district = "".join(str(x) for x in district)
        addr = tip.get("address")
        if isinstance(addr, list):
            addr = "".join(str(x) for x in addr)
        return {
            "label": str(tip.get("name") or address),
            "address": str(addr or district or ""),
            "latitude": lat,
            "longitude": lng,
            "source": "amap",
        }
    raise ValueError("没有找到匹配的地点，请换一个更具体的说法")


def amap_nearby(lat: float, lng: float, api_key: str, limit: int = 6, timeout: float = 8.0) -> List[Dict[str, Any]]:
    """调用高德周边搜索。失败时由调用方回退 local，不把 Key 暴露给前端。"""
    import httpx

    params = {
        "key": api_key,
        "location": f"{lng:.6f},{lat:.6f}",
        "keywords": "医院",
        # 090101 综合医院（含三级甲等）；090102 多为卫生院/社区卫生服务中心，不用于“医院推荐”
        "types": "090101",
        "radius": 15000,
        "offset": 20,
        "page": 1,
        "extensions": "all",
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.get("https://restapi.amap.com/v3/place/around", params=params)
        resp.raise_for_status()
        data = resp.json()
    if str(data.get("status")) != "1":
        raise RuntimeError(f"高德 API 返回失败：{data.get('info') or data.get('infocode')}")

    items: List[Dict[str, Any]] = []
    seen_names = set()
    for poi in (data.get("pois") or []):
        if len(items) >= limit:
            break
        name = str(poi.get("name") or "")
        # 过滤卫生院/社区卫生服务站/诊所，保证“医院推荐”给的是医院
        if not name or any(x in name for x in ("社区卫生服务", "卫生院", "诊所", "服务站")):
            continue
        # 同一家医院的不同院区/西区可能返回多条，用名称前缀去重
        name_key = name[:12]
        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        loc = str(poi.get("location") or "")
        if "," not in loc:
            continue
        lng_s, lat_s = loc.split(",", 1)
        try:
            poi_lng, poi_lat = float(lng_s), float(lat_s)
        except ValueError:
            continue
        distance_m = poi.get("distance")
        try:
            distance_km = round(float(distance_m) / 1000, 2) if distance_m not in (None, "") else round(
                haversine_km(lat, lng, poi_lat, poi_lng), 2
            )
        except (TypeError, ValueError):
            distance_km = round(haversine_km(lat, lng, poi_lat, poi_lng), 2)
        address = poi.get("address")
        if isinstance(address, list):
            address = "".join(str(x) for x in address)
        items.append({
            "name": str(poi.get("name") or "医院"),
            "level": "地图检索",
            "address": str(address or ""),
            "latitude": poi_lat,
            "longitude": poi_lng,
            "distance_km": distance_km,
            "registration_channel": "",
            "registration_url": "",
            "map_url": amap_marker_url(poi_lat, poi_lng, poi.get("name") or "医院"),
            "source": "amap",
        })
    items.sort(key=lambda x: x["distance_km"])
    return items
