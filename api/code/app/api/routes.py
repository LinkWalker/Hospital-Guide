"""API 路由：chat / session / health / history。"""
from fastapi import APIRouter, HTTPException, Query, Response

from ..schemas.models import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    HistoryDetail,
    HistoryListResponse,
    HistoryRestoreResponse,
    LocationResolveResponse,
    NearbyHospitalsResponse,
    SavedLocationRequest,
    SavedLocationResponse,
    SessionResponse,
    SessionState,
)
from ..core.config import get_settings
from ..db.database import query_all
from ..db.saved_location_store import (
    clear_saved_location,
    get_saved_location,
    save_saved_location,
)
from ..modules.geo import amap_input_tip, amap_nearby, nearby_from_rows
from .deps import get_history_store, get_orchestrator

router = APIRouter()


@router.post("/api/v1/chat", response_model=ChatResponse, summary="导诊对话")
def chat(req: ChatRequest):
    orch = get_orchestrator()
    return orch.chat(req.session_id, req.message)


@router.get("/api/v1/session/{session_id}", response_model=SessionResponse, summary="查询会话状态")
def get_session(session_id: str):
    orch = get_orchestrator()
    state = orch.store.load(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return SessionResponse(
        session_id=state.session_id,
        status=state.status,
        collected=state.collected,
        turn_count=state.turn_count,
        interrupted=state.interrupted,
        history=state.history,
    )


# ---------------------------------------------------------------
# 会话历史
# ---------------------------------------------------------------
@router.get("/api/v1/history", response_model=HistoryListResponse, summary="历史记录列表")
def list_history(
    limit: int = Query(20, ge=1, le=100, description="每页条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    store = get_history_store()
    if store is None:
        return HistoryListResponse(items=[], total=0)
    return HistoryListResponse(items=store.list(limit=limit, offset=offset), total=store.count())


@router.get("/api/v1/history/{history_id}", response_model=HistoryDetail, summary="历史记录详情")
def get_history(history_id: str):
    store = get_history_store()
    detail = store.get(history_id) if store else None
    if not detail:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    # response_model 会过滤掉内部的 state 字段，只返回展示所需数据
    return HistoryDetail(**detail)


@router.post(
    "/api/v1/history/{history_id}/restore",
    response_model=HistoryRestoreResponse,
    summary="恢复历史会话（继续对话）",
)
def restore_history(history_id: str):
    store = get_history_store()
    detail = store.get(history_id) if store else None
    if not detail:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    state_data = detail.get("state")
    if not state_data:
        raise HTTPException(status_code=409, detail="该历史记录缺少会话状态，无法继续对话")
    try:
        state = SessionState.model_validate(state_data)
    except Exception:
        raise HTTPException(status_code=409, detail="会话状态损坏，无法继续对话")
    orch = get_orchestrator()
    orch.store.save(state)
    return HistoryRestoreResponse(
        session_id=state.session_id,
        status=state.status,
        messages=detail.get("messages") or [],
    )


@router.delete("/api/v1/history/{history_id}", status_code=204, summary="删除单条历史")
def delete_history(history_id: str):
    store = get_history_store()
    if store is None or not store.delete(history_id):
        raise HTTPException(status_code=404, detail="历史记录不存在")
    return Response(status_code=204)


@router.delete("/api/v1/history", status_code=204, summary="清空全部历史")
def clear_history():
    store = get_history_store()
    if store:
        store.clear()
    return Response(status_code=204)


@router.get(
    "/api/v1/hospitals/geocode",
    response_model=LocationResolveResponse,
    summary="解析用户输入的地点",
)
def geocode_location(address: str = Query(..., min_length=1, max_length=100, description="具体地点，如：旗山校区")):
    address = address.strip()
    if not address:
        raise HTTPException(status_code=422, detail="地点不能为空")
    settings = get_settings()
    if not settings.amap_api_key:
        raise HTTPException(status_code=503, detail="未配置 AMAP_API_KEY，无法解析地点；请手动选择区域")
    try:
        result = amap_input_tip(address, settings.amap_api_key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"无法解析该地点：{exc}")
    return LocationResolveResponse(**result)


@router.get(
    "/api/v1/preferences/location",
    response_model=SavedLocationResponse,
    summary="读取上次保存的医院推荐位置",
)
def read_saved_location():
    data = get_saved_location()
    if not data:
        return Response(status_code=204)
    return SavedLocationResponse(**data)


@router.put(
    "/api/v1/preferences/location",
    response_model=SavedLocationResponse,
    summary="保存医院推荐位置",
)
def put_saved_location(payload: SavedLocationRequest):
    return SavedLocationResponse(**save_saved_location(
        label=payload.label,
        address=payload.address,
        latitude=payload.latitude,
        longitude=payload.longitude,
    ))


@router.delete(
    "/api/v1/preferences/location",
    status_code=204,
    summary="删除保存的医院推荐位置",
)
def delete_saved_location():
    clear_saved_location()
    return Response(status_code=204)


@router.get(
    "/api/v1/hospitals/nearby",
    response_model=NearbyHospitalsResponse,
    summary="附近医院推荐",
)
def nearby_hospitals(
    lat: float = Query(..., ge=-90, le=90, description="纬度（WGS-84/GCJ-02 近似均可）"),
    lng: float = Query(..., ge=-180, le=180, description="经度"),
    limit: int = Query(6, ge=1, le=20, description="返回条数"),
):
    settings = get_settings()
    if settings.amap_api_key:
        try:
            items = amap_nearby(lat, lng, settings.amap_api_key, limit=limit)
            if items:
                return NearbyHospitalsResponse(
                    origin={"lat": lat, "lng": lng},
                    items=items,
                    source="amap",
                    message="来自高德地图周边检索，距离按地图返回结果排序。",
                )
        except Exception:
            # 高德 Key 未开通/限流/网络异常：回退本地医院表，保证功能可用
            pass

    try:
        rows = query_all(
            "SELECT name, level, address, latitude, longitude,"
            " registration_channel, registration_url FROM hospitals"
        )
    except Exception:
        rows = []
    items = nearby_from_rows(rows, lat, lng, limit=limit)
    message = "" if items else "暂无可用的医院坐标数据，请检查 hospitals 表或配置高德 Key。"
    return NearbyHospitalsResponse(
        origin={"lat": lat, "lng": lng},
        items=items,
        source="local",
        message=message,
    )


@router.get("/api/v1/health", response_model=HealthResponse, summary="健康检查")
def health():
    settings = get_settings()
    llm_ready = bool(
        settings.llm_provider != "mock"
        and settings.llm_base_url
        and settings.llm_api_key
        and settings.llm_model
    )
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        llm_ready=llm_ready,
        session_store=settings.session_store,
        retriever=settings.retriever,
        history_enabled=bool(settings.history_enabled),
    )
