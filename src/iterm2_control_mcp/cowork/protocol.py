from __future__ import annotations

import json
from dataclasses import dataclass, field


class ProtocolError(Exception):
    """Raised when an incoming wire message violates the RPC protocol.

    Caught by the cowork server so a structured error Response is written
    back to the client instead of silently closing the connection.
    """


@dataclass
class Request:
    method: str
    params: dict[str, object] = field(default_factory=dict)
    id: int | str | None = None

    def to_json(self) -> str:
        msg: dict[str, object] = {
            "jsonrpc": "2.0",
            "method": self.method,
            "params": self.params,
        }
        if self.id is not None:
            msg["id"] = self.id
        return json.dumps(msg)

    @classmethod
    def from_json(cls, data: str) -> Request:
        try:
            msg = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"invalid JSON: {exc}") from exc
        if not isinstance(msg, dict):
            raise ProtocolError(
                f"request must be a JSON object, got {type(msg).__name__}",
            )
        method = msg.get("method")
        if not isinstance(method, str) or not method:
            raise ProtocolError("missing or non-string 'method' field")
        params = msg.get("params", {})
        if not isinstance(params, dict):
            raise ProtocolError(
                f"'params' must be an object, got {type(params).__name__}",
            )
        return cls(method=method, params=params, id=msg.get("id"))


@dataclass
class Response:
    id: int | str | None
    result: object = None
    error: str | None = None

    def to_json(self) -> str:
        msg: dict[str, object] = {"jsonrpc": "2.0", "id": self.id}
        if self.error:
            msg["error"] = {"code": -1, "message": self.error}
        else:
            msg["result"] = self.result
        return json.dumps(msg)
