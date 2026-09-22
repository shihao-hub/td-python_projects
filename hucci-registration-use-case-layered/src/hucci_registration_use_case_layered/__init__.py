import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from .api import get_enroll, router
from .db import SqliteDb
from .service import EnrollService


def _data_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "language_projects" / "hucci-registration-use-case-layered"
    return Path.home() / ".language_projects" / "hucci-registration-use-case-layered"


db = SqliteDb(_data_path() / "school.db")
enroll_service = EnrollService(db)

app = FastAPI()
app.include_router(router)
app.dependency_overrides[get_enroll] = lambda: enroll_service


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8000)
