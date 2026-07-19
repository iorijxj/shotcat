"""Files 接口响应壳测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1.routes.studio import files as files_route
from app.dependencies import get_db
from app.main import app
from app.models.studio import FileItem
from app.models.types import FileType


class _DummyDB:
    async def get(self, *_args, **_kwargs):
        return None

    async def execute(self, *_args, **_kwargs):
        # 归属校验里 file_usages 查询在这批测试里恒为空（视为公共/未落库使用记录），放行。
        class _EmptyResult:
            def all(self) -> list[tuple]:
                return []

        return _EmptyResult()

    async def delete(self, *_args, **_kwargs) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def refresh(self, *_args, **_kwargs) -> None:
        return None

    def add(self, *_args, **_kwargs) -> None:
        return None


def _override_db(db: _DummyDB):
    async def _get_db() -> AsyncGenerator[_DummyDB, None]:
        yield db

    return _get_db


def test_list_files_requires_project_id_when_scope_filters_set(client: TestClient) -> None:
    # project_id 现在是接口级必填参数（跨用户隔离需要），缺失时由 FastAPI 自身校验拦截为 422，
    # 不再是路由内部的业务级 400（旧的"project_id 与 chapter_title/shot_title 联动"检查已随之作废）。
    db = _DummyDB()
    app.dependency_overrides[get_db] = _override_db(db)
    try:
        response = client.get("/api/v1/studio/files", params={"chapter_title": "第一章"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert "project_id" in body["message"]


def test_get_file_detail_not_found_returns_api_response(client: TestClient, monkeypatch) -> None:
    db = _DummyDB()

    async def _fake_get_file_detail(*_args, **_kwargs):
        raise HTTPException(status_code=404, detail="File not found")

    monkeypatch.setattr(files_route, "get_file_detail_service", _fake_get_file_detail)
    app.dependency_overrides[get_db] = _override_db(db)
    try:
        response = client.get("/api/v1/studio/files/missing")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"code": 404, "message": "File not found", "data": None, "meta": None}


def test_delete_file_returns_empty_envelope(client: TestClient, monkeypatch) -> None:
    db = _DummyDB()

    async def _fake_delete_file(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(files_route, "delete_file", _fake_delete_file)
    app.dependency_overrides[get_db] = _override_db(db)
    try:
        response = client.delete("/api/v1/studio/files/file-1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"code": 200, "message": "success", "data": None, "meta": None}


def test_update_file_meta_returns_success_envelope(client: TestClient, monkeypatch) -> None:
    db = _DummyDB()
    now = datetime.now(UTC)
    file_item = FileItem(
        id="file-1",
        type=FileType.image,
        name="封面图",
        thumbnail="https://example.com/image.png",
        tags=["cover"],
        storage_key="files/image.png",
    )
    file_item.created_at = now
    file_item.updated_at = now

    async def _fake_update_file_meta(*_args, **_kwargs):
        return file_item

    monkeypatch.setattr(files_route, "update_file_meta_service", _fake_update_file_meta)
    app.dependency_overrides[get_db] = _override_db(db)
    try:
        response = client.patch(
            "/api/v1/studio/files/file-1",
            json={"name": "新封面图"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert body["message"] == "success"
    assert body["data"]["id"] == "file-1"
    assert body["data"]["name"] == "封面图"
