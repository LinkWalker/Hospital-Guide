"""应用入口：FastAPI 实例。启动：uvicorn app.main:app --reload"""
import os

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .core.config import get_settings
from .api.routes import router
from .db.database import init_db

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.3.0-alpha")

# 允许前端跨域调用（本地开发 / 前后端分离部署）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # 前端不用 Cookie/凭证；关闭 credentials 才能与 allow_origins=["*"] 正确配合
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def allow_private_network(request, call_next):
    """允许 file:// 页面访问 localhost（Chrome Private Network Access）。

    Starlette 的 CORSMiddleware 默认会拒绝带 PNA 请求头的预检（400），
    因此这里先在外层直接回 200，再交给普通请求/其他中间件处理。
    """
    if request.method == "OPTIONS" and request.headers.get(
        "access-control-request-private-network", ""
    ).lower() == "true":
        origin = request.headers.get("origin") or "*"
        response = Response(status_code=200)
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = (
            "DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT"
        )
        requested_headers = request.headers.get("access-control-request-headers")
        if requested_headers:
            response.headers["Access-Control-Allow-Headers"] = requested_headers
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        response.headers["Access-Control-Max-Age"] = "600"
        response.headers["Vary"] = (
            "Origin, Access-Control-Request-Method, "
            "Access-Control-Request-Headers, Access-Control-Request-Private-Network"
        )
        return response
    response = await call_next(request)
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response

app.include_router(router)

# 演示前端（团队自建页面，与墨刀原型互补）
_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.isdir(_FRONTEND_DIR):
    app.mount("/web", StaticFiles(directory=_FRONTEND_DIR), name="web")


@app.on_event("startup")
def _startup():
    # 启动时自动建库 + 灌种子（幂等，已建则跳过）
    init_db()


@app.get("/", include_in_schema=False)
def root():
    index = os.path.join(_FRONTEND_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"app": settings.app_name, "doc": "/docs", "health": "/api/v1/health"}


@app.get("/app.js", include_in_schema=False)
def frontend_app_js():
    """前端逻辑单独成文件，便于维护；根路径和 /web 都能访问。"""
    path = os.path.join(_FRONTEND_DIR, "app.js")
    if os.path.exists(path):
        return FileResponse(path, media_type="application/javascript")
    raise HTTPException(status_code=404, detail="app.js not found")
