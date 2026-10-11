from pathlib import Path
from typing import Optional
import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from docsearch.config import AppConfig, load_config
from docsearch.service import IndexStats, SearchResponse, SearchService
from docsearch.watcher import DirectoryWatcher


def create_app(config: Optional[AppConfig] = None) -> FastAPI:
    cfg = config or load_config()
    service = SearchService(cfg)
    app = FastAPI(
        title="docsearch API",
        description="Local Semantic Document Search Engine",
        version="0.1.0",
    )

    # 启动后台文件监听器
    watcher = DirectoryWatcher(service)

    @app.on_event("startup")
    def on_startup():
        try:
            watcher.start()
        except Exception as e:
            print(f"[docsearch server] 启动 Watcher 异常: {e}")

    @app.on_event("shutdown")
    def on_shutdown():
        try:
            watcher.stop()
        except Exception:
            pass

    @app.get("/api/search", response_model=SearchResponse)
    def search_docs(
        q: str = Query(..., description="模糊语义检索自然语言句子"),
        top_k: int = Query(5, ge=1, le=50, description="返回条目上限"),
    ):
        return service.search(query=q, top_k=top_k)

    @app.get("/api/status", response_model=IndexStats)
    def get_status():
        return service.get_stats()

    @app.post("/api/reindex")
    def reindex_all(force: bool = Query(False, description="是否强制全量重建")):
        stats = service.index_all(force=force)
        return {"status": "ok", "stats": stats}

    # 挂载 WebUI 静态文件
    web_dir = Path(__file__).parent / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

        @app.get("/")
        def serve_index():
            return FileResponse(str(web_dir / "index.html"))

    return app


def run_server(config: AppConfig, host: str = "127.0.0.1", port: int = 8765):
    app = create_app(config)
    print(f"\n[docsearch] 🚀 WebUI 正在运行: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")