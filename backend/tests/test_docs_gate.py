"""production 關閉 API schema 面（/docs /redoc /openapi.json）的環境閘測試。

docs_kwargs 為純函式（main.py 建 app 時展開）——直接鎖兩態回傳，
免 reload app（app 於 import 時定型，reload 反而引入測試間耦合）。
"""

from unittest import mock

from app.api.main import docs_kwargs


def test_docs_enabled_outside_production():
    """非 production：不覆蓋 FastAPI 預設（/docs /redoc /openapi.json 照常）。"""
    with mock.patch("app.core.config.is_production", return_value=False):
        assert docs_kwargs() == {}


def test_docs_disabled_in_production():
    """production：三個 schema 入口全關。"""
    with mock.patch("app.core.config.is_production", return_value=True):
        assert docs_kwargs() == {
            "docs_url": None,
            "redoc_url": None,
            "openapi_url": None,
        }
