from __future__ import annotations

import json

import pytest

from iterm2_control_mcp.cowork.protocol import ProtocolError, Request, Response


def test_request_to_json() -> None:
    req = Request(method="iterm_status", params={}, id=1)
    data = json.loads(req.to_json())
    assert data["jsonrpc"] == "2.0"
    assert data["method"] == "iterm_status"
    assert data["id"] == 1


def test_request_from_json() -> None:
    msg = {"jsonrpc": "2.0", "method": "iterm_read_output", "params": {"lines": 10}, "id": 2}
    raw = json.dumps(msg)
    req = Request.from_json(raw)
    assert req.method == "iterm_read_output"
    assert req.params["lines"] == 10
    assert req.id == 2


def test_response_success() -> None:
    resp = Response(id=1, result="ok")
    data = json.loads(resp.to_json())
    assert data["result"] == "ok"
    assert "error" not in data


def test_response_error() -> None:
    resp = Response(id=1, error="something broke")
    data = json.loads(resp.to_json())
    assert data["error"]["message"] == "something broke"


def test_request_from_json_invalid_json() -> None:
    with pytest.raises(ProtocolError, match="invalid JSON"):
        Request.from_json("not-json{")


def test_request_from_json_not_an_object() -> None:
    with pytest.raises(ProtocolError, match="must be a JSON object"):
        Request.from_json("[1, 2, 3]")


def test_request_from_json_missing_method() -> None:
    with pytest.raises(ProtocolError, match="method"):
        Request.from_json(json.dumps({"params": {}, "id": 1}))


def test_request_from_json_empty_method() -> None:
    with pytest.raises(ProtocolError, match="method"):
        Request.from_json(json.dumps({"method": "", "id": 1}))


def test_request_from_json_non_string_method() -> None:
    with pytest.raises(ProtocolError, match="method"):
        Request.from_json(json.dumps({"method": 42, "id": 1}))


def test_request_from_json_non_object_params() -> None:
    with pytest.raises(ProtocolError, match="params"):
        Request.from_json(json.dumps({"method": "x", "params": [1, 2]}))
