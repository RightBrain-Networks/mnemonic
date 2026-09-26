"""A disconnected body reader is an expected transport event, not a traceback."""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from mnemonic_api.application.handlers import install_exception_handlers


def test_client_disconnect_is_handled_without_logging_private_request_details(caplog):
    app = FastAPI()
    install_exception_handlers(app)

    @app.post("/body")
    async def body(request: Request):
        raise ClientDisconnect("private-marker")

    response = TestClient(app).post("/body", content="private-marker")
    assert response.status_code == 499
    assert response.content == b""
    assert "private-marker" not in caplog.text
