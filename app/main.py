"""FastAPI 入口：装配应用、静态资源、生命周期。"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth, db, routes
from .services import worker

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    from . import auth
    auth._ensure_seed_users()
    from .sources import cookies as cookie_svc
    moved = cookie_svc.migrate_from_db()
    if moved:
        print(f"[init] 已从旧存储迁移 {moved} 份 Cookie 到 {cookie_svc._file()}")
    worker.start()
    yield
    worker.stop()


app = FastAPI(
    title="工商企业聚合信息查询系统",
    lifespan=lifespan,
    docs_url=None,      # 自用工具不暴露交互式文档
    redoc_url=None,
    openapi_url=None,
)

@app.middleware("http")
async def no_cache_assets(request, call_next):
    """HTML/JS/CSS 禁用缓存：避免浏览器缓存旧前端导致登录/功能异常。"""
    response = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith("/static") or p.startswith("/index"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


app.include_router(auth.router)
app.include_router(routes.public)
app.include_router(routes.protected)
app.include_router(routes.admin)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
