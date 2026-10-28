"""Bounded guest-side model gateway for the OpenAI-compatible chat route.

Standalone client used by the agent-zone broker adapter. It validates an
OpenAI Chat Completions request, pins the model name, forwards exactly one
POST to a fixed loopback origin with an optional bearer token, and returns a
response handle that streams bounded bytes or parsed server-sent events.

The caller (broker) owns reading the request body, duplicate JSON key
detection and writing the reply back to the guest. This module implements no
server, follows no redirects, performs no DNS, and accepts no caller-supplied
headers, upstreams or credentials. Message content strings are inert prose:
they are length-checked and never interpreted.
"""

from __future__ import annotations

import http.client
import json
import math
import re
import socket
import ssl
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

__all__ = ["GatewayError", "validate_request", "ModelGateway", "GatewayResponse"]

# Request-side bounds. The broker enforces the raw body cap before parsing;
# these bounds apply to the parsed document.
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 20000
MAX_TEXT_LEN = 1024 * 1024
MAX_MESSAGES = 256
MAX_TOOLS = 64
MAX_TOOL_CALLS = 64
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
MAX_ID_LEN = 128
MAX_STOPS = 16
MAX_MAX_TOKENS = 128000

# Schema-side bounds.
MAX_SCHEMA_DEPTH = 16
MAX_SCHEMA_NODES = 512
MAX_SCHEMA_KEYS = 64
MAX_ENUM_ITEMS = 64

# Response-side bounds (defaults; caller may lower them per gateway).
DEFAULT_CHUNK_SIZE = 65536
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_LINE_BYTES = 1024 * 1024
DEFAULT_MAX_EVENTS = 4096
DEFAULT_DEADLINE_SECONDS = 300.0
MAX_UPSTREAM_HEADER_BYTES = 32 * 1024
MAX_UPSTREAM_HEADER_FIELDS = 64
MAX_UPSTREAM_HEADER_LINE_BYTES = 8 * 1024

_ALLOWED_TOP_KEYS = frozenset(
    {
        "model",
        "messages",
        "stream",
        "stream_options",
        "store",
        "max_tokens",
        "temperature",
        "top_p",
        "stop",
        "tools",
    }
)
_ALLOWED_SCHEMA_KEYS = frozenset(
    {
        "type",
        "enum",
        "const",
        "properties",
        "required",
        "items",
        "additionalProperties",
        "description",
        "title",
        "default",
        "format",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "anyOf",
        "oneOf",
        "allOf",
        "not",
        "nullable",
        "$defs",
        "definitions",
        "$ref",
    }
)
_ALLOWED_JSON_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean", "null"}
)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=\-]{1,512}$")


class GatewayError(Exception):
    """Safe refusal. Carries an HTTP status and a machine-readable code."""

    def __init__(self, status: int, code: str, message: str = "") -> None:
        self.status = status
        self.code = code
        self.message = message or code
        super().__init__("[%d] %s: %s" % (status, code, self.message))


def _refuse(status: int, code: str, message: str = "") -> "GatewayError":
    return GatewayError(status, code, message)


def _walk_limits(value: Any, depth: int, budget: List[int], where: str) -> None:
    """Enforce depth, node-count and string-length bounds on parsed JSON."""
    if depth > MAX_DEPTH:
        raise _refuse(400, "too_deep", where)
    budget[0] -= 1
    if budget[0] < 0:
        raise _refuse(400, "too_large", where)
    if isinstance(value, str):
        if len(value) > MAX_TEXT_LEN:
            raise _refuse(400, "text_too_long", where)
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise _refuse(400, "invalid_json", where)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > MAX_TEXT_LEN:
                raise _refuse(400, "text_too_long", where)
            _walk_limits(item, depth + 1, budget, where)
    elif isinstance(value, list):
        for item in value:
            _walk_limits(item, depth + 1, budget, where)
    elif isinstance(value, float) and not math.isfinite(value):
        raise _refuse(400, "invalid_json", where)
    elif value is not None and not isinstance(value, (int, float, bool)):
        raise _refuse(400, "invalid_json", where)


def _strict_json(data: Any, where: str) -> Any:
    def pairs(items: Any) -> Dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError("nonfinite number")

    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
        _walk_limits(value, 0, [MAX_NODES], where)
        return value
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise _refuse(400, "invalid_json", where)


def _require_exact_keys(obj: Dict[str, Any], allowed: frozenset, where: str) -> None:
    extra = sorted(set(obj.keys()) - set(allowed))
    if extra:
        raise _refuse(400, "unknown_field", "%s: %s" % (where, ",".join(extra)))


def _check_str(value: Any, where: str, max_len: int = MAX_TEXT_LEN) -> str:
    if not isinstance(value, str) or len(value) > max_len:
        raise _refuse(400, "invalid_field", where)
    return value


def _resolve_ref(root: Dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/") and ref != "#":
        raise _refuse(400, "external_schema_ref", ref[:128])
    node: Any = root
    parts = [] if ref == "#" else ref[2:].split("/")
    while parts:
        key = parts.pop(0)
        if not isinstance(node, dict) or key not in node:
            raise _refuse(400, "dangling_schema_ref", ref[:128])
        if key in ("properties", "$defs", "definitions"):
            if not parts:
                raise _refuse(400, "invalid_schema_ref", ref[:128])
            name = parts.pop(0)
            if re.search(r"~(?![01])", name):
                raise _refuse(400, "invalid_schema_ref", ref[:128])
            name = name.replace("~1", "/").replace("~0", "~")
            mapping = node[key]
            if not isinstance(mapping, dict) or name not in mapping:
                raise _refuse(400, "dangling_schema_ref", ref[:128])
            node = mapping[name]
        elif key in ("anyOf", "oneOf", "allOf"):
            if not parts or not re.fullmatch(r"0|[1-9][0-9]*", parts[0]):
                raise _refuse(400, "invalid_schema_ref", ref[:128])
            index = int(parts.pop(0))
            if not isinstance(node[key], list) or index >= len(node[key]):
                raise _refuse(400, "dangling_schema_ref", ref[:128])
            node = node[key][index]
        elif key in ("items", "not", "additionalProperties"):
            node = node[key]
        else:
            raise _refuse(400, "invalid_schema_ref", ref[:128])
    if not isinstance(node, (dict, bool)):
        raise _refuse(400, "dangling_schema_ref", ref[:128])
    return node


def _schema_children(node: Dict[str, Any]) -> Iterator[Any]:
    for key in ("properties", "$defs", "definitions"):
        yield from node.get(key, {}).values()
    for key in ("anyOf", "oneOf", "allOf"):
        yield from node.get(key, [])
    for key in ("items", "not", "additionalProperties"):
        if key in node:
            yield node[key]


def _check_schema_refs(root: Dict[str, Any]) -> None:
    def visit(node: Any, ancestors: set, depth: int, budget: List[int]) -> None:
        budget[0] -= 1
        if depth > MAX_SCHEMA_DEPTH or budget[0] < 0:
            raise _refuse(400, "schema_too_large")
        if isinstance(node, bool):
            return
        if id(node) in ancestors:
            raise _refuse(400, "schema_reference_cycle")
        path = ancestors | {id(node)}
        for child in _schema_children(node):
            visit(child, path, depth + 1, budget)
        if "$ref" in node:
            visit(_resolve_ref(root, node["$ref"]), path, depth + 1, budget)
    visit(root, set(), 0, [MAX_SCHEMA_NODES])


def _validate_schema(node: Any, root: Dict[str, Any], depth: int, budget: List[int], where: str) -> None:
    """Bounded explicit JSON Schema grammar. Unknown keywords are rejected."""
    if depth > MAX_SCHEMA_DEPTH:
        raise _refuse(400, "schema_too_deep", where)
    budget[0] -= 1
    if budget[0] < 0:
        raise _refuse(400, "schema_too_large", where)
    if isinstance(node, bool):
        return
    if not isinstance(node, dict):
        raise _refuse(400, "invalid_schema", where)
    if len(node) > MAX_SCHEMA_KEYS:
        raise _refuse(400, "schema_too_large", where)
    _require_exact_keys(node, _ALLOWED_SCHEMA_KEYS, where)
    for key, value in node.items():
        sub = "%s/%s" % (where, key)
        if key == "type":
            if isinstance(value, str):
                if value not in _ALLOWED_JSON_TYPES:
                    raise _refuse(400, "invalid_schema", sub)
            elif isinstance(value, list) and value:
                for t in value:
                    if not isinstance(t, str) or t not in _ALLOWED_JSON_TYPES:
                        raise _refuse(400, "invalid_schema", sub)
            else:
                raise _refuse(400, "invalid_schema", sub)
        elif key == "enum":
            if not isinstance(value, list) or not value or len(value) > MAX_ENUM_ITEMS:
                raise _refuse(400, "invalid_schema", sub)
            for item in value:
                if isinstance(item, (dict, list)):
                    raise _refuse(400, "invalid_schema", sub)
                if isinstance(item, float) and not math.isfinite(item):
                    raise _refuse(400, "invalid_schema", sub)
                if isinstance(item, str) and len(item) > MAX_TEXT_LEN:
                    raise _refuse(400, "invalid_schema", sub)
        elif key == "const":
            if isinstance(value, (dict, list)):
                raise _refuse(400, "invalid_schema", sub)
            if isinstance(value, float) and not math.isfinite(value):
                raise _refuse(400, "invalid_schema", sub)
        elif key == "required":
            if not isinstance(value, list) or len(value) > MAX_SCHEMA_KEYS:
                raise _refuse(400, "invalid_schema", sub)
            seen = set()
            for name in value:
                if not isinstance(name, str) or len(name) > 256 or name in seen:
                    raise _refuse(400, "invalid_schema", sub)
                seen.add(name)
        elif key == "properties":
            if not isinstance(value, dict) or len(value) > MAX_SCHEMA_KEYS:
                raise _refuse(400, "invalid_schema", sub)
            for name, child in value.items():
                if not isinstance(name, str) or len(name) > 256:
                    raise _refuse(400, "invalid_schema", sub)
                _validate_schema(child, root, depth + 1, budget, "%s/%s" % (sub, name))
        elif key == "additionalProperties":
            if isinstance(value, bool):
                continue
            _validate_schema(value, root, depth + 1, budget, sub)
        elif key in ("items", "not"):
            if isinstance(value, list):
                raise _refuse(400, "invalid_schema", sub)
            _validate_schema(value, root, depth + 1, budget, sub)
        elif key in ("anyOf", "oneOf", "allOf"):
            if not isinstance(value, list) or not value or len(value) > MAX_ENUM_ITEMS:
                raise _refuse(400, "invalid_schema", sub)
            for child in value:
                _validate_schema(child, root, depth + 1, budget, sub)
        elif key in ("$defs", "definitions"):
            if not isinstance(value, dict) or len(value) > MAX_SCHEMA_KEYS:
                raise _refuse(400, "invalid_schema", sub)
            for name, child in value.items():
                if not isinstance(name, str) or len(name) > 256:
                    raise _refuse(400, "invalid_schema", sub)
                _validate_schema(child, root, depth + 1, budget, "%s/%s" % (sub, name))
        elif key == "$ref":
            _check_str(value, sub, 4096)
            _resolve_ref(root, value)
        elif key == "pattern":
            _check_str(value, sub, 1024)
            try:
                re.compile(value)
            except (re.error, RecursionError):
                raise _refuse(400, "invalid_schema", sub)
        elif key in ("description", "title", "format"):
            _check_str(value, sub, 4096)
        elif key == "default":
            if isinstance(value, (dict, list)):
                _walk_limits(value, 0, [MAX_SCHEMA_NODES], sub)
            elif isinstance(value, float) and not math.isfinite(value):
                raise _refuse(400, "invalid_schema", sub)
        elif key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
                raise _refuse(400, "invalid_schema", sub)
        elif key in ("minLength", "maxLength", "minItems", "maxItems"):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise _refuse(400, "invalid_schema", sub)
        elif key in ("uniqueItems", "nullable"):
            if not isinstance(value, bool):
                raise _refuse(400, "invalid_schema", sub)


def _validate_tool_calls(value: Any, declared: set, where: str, seen_ids: set) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_TOOL_CALLS:
        raise _refuse(400, "invalid_tool_calls", where)
    out = []
    for index, call in enumerate(value):
        sub = "%s[%d]" % (where, index)
        if not isinstance(call, dict):
            raise _refuse(400, "invalid_tool_calls", sub)
        _require_exact_keys(call, frozenset({"id", "type", "function"}), sub)
        if call.get("type") != "function":
            raise _refuse(400, "unsupported_tool_type", sub)
        call_id = _check_str(call.get("id"), sub + "/id", MAX_ID_LEN)
        if not call_id or call_id in seen_ids:
            raise _refuse(400, "invalid_tool_calls", sub + "/id")
        seen_ids.add(call_id)
        fn = call.get("function")
        if not isinstance(fn, dict):
            raise _refuse(400, "invalid_tool_calls", sub + "/function")
        _require_exact_keys(fn, frozenset({"name", "arguments"}), sub + "/function")
        name = _check_str(fn.get("name"), sub + "/function/name", 64)
        if not TOOL_NAME_RE.fullmatch(name):
            raise _refuse(400, "invalid_tool_name", sub + "/function/name")
        if name not in declared:
            raise _refuse(400, "undeclared_tool", name)
        arguments = _check_str(fn.get("arguments"), sub + "/function/arguments", MAX_TEXT_LEN)
        try:
            parsed = _strict_json(arguments, sub + "/function/arguments")
        except GatewayError:
            raise _refuse(400, "invalid_tool_arguments", sub + "/function/arguments")
        if not isinstance(parsed, dict):
            raise _refuse(400, "invalid_tool_arguments", sub + "/function/arguments")
        out.append({"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}})
    return out


def _validate_message(index: int, msg: Any, declared: set, answered: Dict[str, str], used: set) -> Dict[str, Any]:
    where = "messages[%d]" % index
    if not isinstance(msg, dict):
        raise _refuse(400, "invalid_message", where)
    role = msg.get("role")
    if role == "system":
        _require_exact_keys(msg, frozenset({"role", "content"}), where)
        return {"role": "system", "content": _check_str(msg.get("content"), where + "/content")}
    if role == "user":
        _require_exact_keys(msg, frozenset({"role", "content"}), where)
        content = msg.get("content")
        if isinstance(content, str):
            _check_str(content, where + "/content")
            return {"role": "user", "content": content}
        # Text-only content parts; image/audio/video parts are rejected here.
        if not isinstance(content, list) or not content:
            raise _refuse(400, "invalid_content", where + "/content")
        parts = []
        for pindex, part in enumerate(content):
            sub = "%s/content[%d]" % (where, pindex)
            if not isinstance(part, dict):
                raise _refuse(400, "invalid_content", sub)
            _require_exact_keys(part, frozenset({"type", "text"}), sub)
            if part.get("type") != "text":
                raise _refuse(400, "unsupported_content_type", sub)
            parts.append({"type": "text", "text": _check_str(part.get("text"), sub + "/text")})
        return {"role": "user", "content": parts}
    if role == "assistant":
        _require_exact_keys(msg, frozenset({"role", "content", "tool_calls"}), where)
        out: Dict[str, Any] = {"role": "assistant"}
        content = msg.get("content")
        if content is not None:
            out["content"] = _check_str(content, where + "/content")
        else:
            out["content"] = None
        if "tool_calls" in msg:
            out["tool_calls"] = _validate_tool_calls(msg["tool_calls"], declared, where + "/tool_calls", used)
            for call in out["tool_calls"]:
                answered[call["id"]] = call["function"]["name"]
        elif out["content"] is None or out["content"] == "":
            raise _refuse(400, "empty_assistant_message", where)
        return out
    if role == "tool":
        _require_exact_keys(msg, frozenset({"role", "tool_call_id", "content", "name"}), where)
        tool_call_id = _check_str(msg.get("tool_call_id"), where + "/tool_call_id", MAX_ID_LEN)
        if tool_call_id not in answered:
            raise _refuse(400, "orphan_tool_result", where)
        name = msg.get("name")
        if name is not None:
            _check_str(name, where + "/name", 64)
            if name != answered[tool_call_id]:
                raise _refuse(400, "tool_result_name_mismatch", where)
        del answered[tool_call_id]
        return {"role": "tool", "tool_call_id": tool_call_id, "content": _check_str(msg.get("content"), where + "/content")}
    raise _refuse(400, "unsupported_role", str(role))


def _validate_tools(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_TOOLS:
        raise _refuse(400, "invalid_tools", "tools")
    out = []
    names = set()
    for index, tool in enumerate(value):
        where = "tools[%d]" % index
        if not isinstance(tool, dict):
            raise _refuse(400, "invalid_tools", where)
        _require_exact_keys(tool, frozenset({"type", "function"}), where)
        if tool.get("type") != "function":
            raise _refuse(400, "unsupported_tool_type", where)
        fn = tool.get("function")
        if not isinstance(fn, dict):
            raise _refuse(400, "invalid_tools", where + "/function")
        _require_exact_keys(fn, frozenset({"name", "description", "parameters", "strict"}), where + "/function")
        name = _check_str(fn.get("name"), where + "/function/name", 64)
        if not TOOL_NAME_RE.fullmatch(name):
            raise _refuse(400, "invalid_tool_name", where + "/function/name")
        if name in names:
            raise _refuse(400, "duplicate_tool_name", name)
        names.add(name)
        entry: Dict[str, Any] = {"type": "function", "function": {"name": name}}
        if "description" in fn:
            entry["function"]["description"] = _check_str(fn["description"], where + "/function/description")
        if "parameters" in fn:
            params = fn["parameters"]
            if not isinstance(params, (dict, bool)):
                raise _refuse(400, "invalid_schema", where + "/function/parameters")
            root = params if isinstance(params, dict) else {}
            _validate_schema(params, root, 0, [MAX_SCHEMA_NODES], where + "/function/parameters")
            if isinstance(params, dict):
                _check_schema_refs(params)
            entry["function"]["parameters"] = params
        if "strict" in fn:
            if not isinstance(fn["strict"], bool):
                raise _refuse(400, "invalid_tools", where + "/function/strict")
            entry["function"]["strict"] = fn["strict"]
        out.append(entry)
    return out


def validate_request(document: Any, fixed_model_name: str) -> Dict[str, Any]:
    """Validate an OpenAI Chat Completions document and return the payload to forward.

    The caller supplies an already-parsed JSON document (duplicate-key
    detection is the broker's job). The model name is fixed: a caller `model`
    must equal `fixed_model_name` and the returned payload always carries it.
    Only function tools declared in the request are permitted; they execute
    inside the guest. Every other key is refused, so upstream, base_url,
    auth, hosted-tool and connector overrides cannot pass.
    """
    if not isinstance(document, dict):
        raise _refuse(400, "invalid_request", "document must be an object")
    _walk_limits(document, 0, [MAX_NODES], "request")
    _require_exact_keys(document, _ALLOWED_TOP_KEYS, "request")

    if "model" in document and document["model"] != fixed_model_name:
        raise _refuse(400, "model_override", "caller model must equal the fixed model")

    stream = document.get("stream", False)
    if not isinstance(stream, bool):
        raise _refuse(400, "invalid_field", "stream")

    out: Dict[str, Any] = {"model": fixed_model_name, "stream": stream}

    messages = document.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise _refuse(400, "invalid_messages", "messages")

    declared: set = set()
    if "tools" in document:
        out["tools"] = _validate_tools(document["tools"])
        declared = {tool["function"]["name"] for tool in out["tools"]}

    validated: List[Dict[str, Any]] = []
    answered: Dict[str, str] = {}
    used: set = set()
    for index, msg in enumerate(messages):
        if answered and (not isinstance(msg, dict) or msg.get("role") != "tool"):
            raise _refuse(400, "pending_tool_results")
        validated.append(_validate_message(index, msg, declared, answered, used))
    if answered:
        raise _refuse(400, "pending_tool_results")
    out["messages"] = validated

    if "stream_options" in document:
        options = document["stream_options"]
        if not isinstance(options, dict):
            raise _refuse(400, "invalid_field", "stream_options")
        _require_exact_keys(options, frozenset({"include_usage"}), "stream_options")
        if not isinstance(options.get("include_usage"), bool):
            raise _refuse(400, "invalid_field", "stream_options/include_usage")
        out["stream_options"] = {"include_usage": options["include_usage"]}

    if "store" in document:
        if document["store"] is not False:
            raise _refuse(400, "invalid_field", "store")
        out["store"] = False

    if "max_tokens" in document:
        value = document["max_tokens"]
        if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= MAX_MAX_TOKENS):
            raise _refuse(400, "invalid_field", "max_tokens")
        out["max_tokens"] = value

    for key, low, high in (("temperature", 0.0, 2.0), ("top_p", 0.0, 1.0)):
        if key in document:
            value = document[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise _refuse(400, "invalid_field", key)
            try:
                value = float(value)
            except OverflowError:
                raise _refuse(400, "invalid_field", key)
            if not math.isfinite(value) or not (low <= value <= high):
                raise _refuse(400, "invalid_field", key)
            out[key] = value

    if "stop" in document:
        value = document["stop"]
        if isinstance(value, str):
            _check_str(value, "stop", 512)
            out["stop"] = value
        elif isinstance(value, list) and value and len(value) <= MAX_STOPS:
            for item in value:
                _check_str(item, "stop", 512)
            out["stop"] = list(value)
        else:
            raise _refuse(400, "invalid_field", "stop")

    return out


def _parse_origin(origin: str) -> Tuple[str, str, int]:
    """Return (scheme, host, port) for a fixed HTTP(S) loopback origin.

    Only literal loopback hosts are accepted; no DNS resolution occurs.
    """
    if not isinstance(origin, str) or not origin:
        raise _refuse(400, "invalid_origin", "origin")
    scheme, sep, rest = origin.partition("://")
    if not sep or scheme not in ("http", "https"):
        raise _refuse(400, "invalid_origin", "scheme")
    if "/" in rest or "?" in rest or "#" in rest:
        raise _refuse(400, "invalid_origin", "origin must not carry a path")
    hostport = rest
    port: Optional[int] = None
    if hostport.startswith("["):
        host, bracket, tail = hostport.partition("]")
        host = host[1:]
        if not bracket:
            raise _refuse(400, "invalid_origin", "host")
        if tail:
            if not tail.startswith(":"):
                raise _refuse(400, "invalid_origin", "port")
            port_text = tail[1:]
        else:
            port_text = ""
    else:
        host, _, port_text = hostport.partition(":")
    if host not in _LOOPBACK_HOSTS:
        raise _refuse(400, "invalid_origin", "host must be literal loopback")
    if port_text:
        if not port_text.isdigit():
            raise _refuse(400, "invalid_origin", "port")
        port = int(port_text)
        if not (1 <= port <= 65535):
            raise _refuse(400, "invalid_origin", "port")
    else:
        port = 443 if scheme == "https" else 80
    return scheme, host, port


def _connect_literal(address: Tuple[str, int], timeout: float,
                     source_address: Any = None) -> socket.socket:
    host, port = address
    if host not in _LOOPBACK_HOSTS or source_address is not None:
        raise _refuse(400, "invalid_origin")
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect((host, port))
        return sock
    except BaseException:
        sock.close()
        raise


def _check_upstream_headers(resp: http.client.HTTPResponse) -> None:
    # These normalized-field caps follow stdlib's finite 64 KiB/100-line parser caps.
    fields = resp.getheaders()
    total = len(resp.reason) + 16
    if len(fields) > MAX_UPSTREAM_HEADER_FIELDS or total > MAX_UPSTREAM_HEADER_LINE_BYTES:
        raise _refuse(502, "upstream_headers_too_large")
    for name, value in fields:
        size = len(name) + len(value) + 4
        total += size
        if size > MAX_UPSTREAM_HEADER_LINE_BYTES or total > MAX_UPSTREAM_HEADER_BYTES:
            raise _refuse(502, "upstream_headers_too_large")
    lengths = resp.headers.get_all("Content-Length", [])
    encodings = resp.headers.get_all("Transfer-Encoding", [])
    if (len(lengths) > 1 or len(encodings) > 1 or (lengths and encodings)
            or (lengths and not re.fullmatch(r"[0-9]+", lengths[0].strip()))
            or (lengths and resp.length is None)
            or (encodings and encodings[0].strip().lower() != "chunked")):
        raise _refuse(502, "upstream_framing")


class _FramedHTTPResponse(http.client.HTTPResponse):
    def _get_chunk_left(self) -> Optional[int]:
        if not self.chunk_left:
            if self.chunk_left is not None and self._safe_read(2) != b"\r\n":
                raise http.client.IncompleteRead(b"")
            line = self.fp.readline(MAX_UPSTREAM_HEADER_LINE_BYTES + 1)
            size = line[:-2].partition(b";")[0]
            if (not line.endswith(b"\r\n") or len(line) > MAX_UPSTREAM_HEADER_LINE_BYTES
                    or not re.fullmatch(rb"[0-9a-fA-F]+", size)):
                raise http.client.IncompleteRead(b"")
            self.chunk_left = int(size, 16)
            if self.chunk_left == 0:
                self._read_and_discard_trailer()
                self._close_conn()
                self.chunk_left = None
        return self.chunk_left

    def _read_and_discard_trailer(self) -> None:
        # Stdlib permits EOF here for compatibility; completion requires the blank line.
        total = 0
        for _ in range(MAX_UPSTREAM_HEADER_FIELDS + 1):
            line = self.fp.readline(MAX_UPSTREAM_HEADER_LINE_BYTES + 1)
            total += len(line)
            if not line or not line.endswith(b"\r\n") or len(line) > MAX_UPSTREAM_HEADER_LINE_BYTES or total > MAX_UPSTREAM_HEADER_BYTES:
                raise http.client.IncompleteRead(b"")
            if line == b"\r\n":
                return
            name, separator, _ = line.partition(b":")
            if not separator or not re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                raise http.client.IncompleteRead(b"")
        raise http.client.IncompleteRead(b"")


class _Deadline:
    def __init__(self, conn: http.client.HTTPConnection, seconds: float) -> None:
        self.conn = conn
        self.response: Optional[http.client.HTTPResponse] = None
        self.until = time.monotonic() + seconds
        self.expired = threading.Event()
        self.timer = threading.Timer(seconds, self._expire)
        self.timer.daemon = True
        self.timer.start()

    def _expire(self) -> None:
        self.expired.set()
        # shutdown interrupts a buffered read even when makefile owns the descriptor.
        candidates = [self.conn.sock]
        fp = getattr(self.response, "fp", None)
        candidates.append(getattr(getattr(fp, "raw", None), "_sock", None))
        for sock in candidates:
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        # HTTPResponse owns mutable buffered-reader state; its reader closes it.
        for sock in candidates:
            if sock is not None:
                sock.close()

    def check(self) -> None:
        if self.expired.is_set() or time.monotonic() >= self.until:
            raise _refuse(504, "deadline_exceeded")

    def cancel(self) -> None:
        self.timer.cancel()


class _ChatResponseValidator:
    def __init__(self, model: str, declared: set) -> None:
        self.model = model
        self.declared = declared
        self.response_id: Optional[str] = None
        self.finished = False
        self.calls: Dict[int, Dict[str, Any]] = {}
        self.usage: Optional[Dict[str, Any]] = None
        self.finish_reason: Optional[str] = None

    def envelope(self, event: Any, streaming: bool) -> None:
        if not isinstance(event, dict):
            raise _refuse(502, "invalid_response")
        _require_exact_keys(event, frozenset({"id", "object", "created", "model", "choices", "usage", "system_fingerprint"}), "response")
        if event.get("object") != ("chat.completion.chunk" if streaming else "chat.completion"):
            raise _refuse(502, "invalid_response")
        identity = _check_str(event.get("id"), "response/id", 256)
        if not identity or event.get("model") != self.model:
            raise _refuse(502, "response_identity")
        if self.response_id is not None and identity != self.response_id:
            raise _refuse(502, "response_identity")
        self.response_id = identity
        if "created" in event and (type(event["created"]) is not int or event["created"] < 0):
            raise _refuse(502, "invalid_response")
        if event.get("system_fingerprint") is not None:
            _check_str(event["system_fingerprint"], "response/system_fingerprint", 256)
        usage = event.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise _refuse(502, "invalid_usage")
            allowed = {"prompt_tokens", "completion_tokens", "total_tokens", "prompt_tokens_details", "completion_tokens_details"}
            _require_exact_keys(usage, frozenset(allowed), "usage")
            for key, value in usage.items():
                if key.endswith("_details"):
                    if value is None:
                        continue
                    if not isinstance(value, dict):
                        raise _refuse(502, "invalid_usage")
                    detail_keys = {"cached_tokens", "audio_tokens"} if key == "prompt_tokens_details" else {"reasoning_tokens", "audio_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens"}
                    _require_exact_keys(value, frozenset(detail_keys), "usage/details")
                    values = value.values()
                else:
                    values = [value]
                if any(type(n) is not int or n < 0 or n > 2 ** 53 for n in values):
                    raise _refuse(502, "invalid_usage")
            self.usage = usage

    def event(self, event: Dict[str, Any]) -> None:
        self.envelope(event, True)
        choices = event.get("choices")
        if not isinstance(choices, list) or len(choices) > 1:
            raise _refuse(502, "invalid_response")
        if not choices:
            if event.get("usage") is None or not self.finished:
                raise _refuse(502, "invalid_response")
            return
        if self.finished:
            raise _refuse(502, "response_after_finish")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise _refuse(502, "invalid_response")
        _require_exact_keys(choice, frozenset({"index", "delta", "finish_reason", "logprobs"}), "choice")
        if type(choice.get("index")) is not int or choice["index"] != 0 or choice.get("logprobs") is not None:
            raise _refuse(502, "invalid_response")
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            raise _refuse(502, "invalid_response")
        _require_exact_keys(delta, frozenset({"role", "content", "tool_calls", "reasoning_content", "reasoning", "reasoning_text"}), "delta")
        if "role" in delta and delta["role"] != "assistant":
            raise _refuse(502, "invalid_response")
        for key in ("content", "reasoning_content", "reasoning", "reasoning_text"):
            if delta.get(key) is not None:
                _check_str(delta[key], "delta/" + key)
        if "tool_calls" in delta:
            calls = delta["tool_calls"]
            if not isinstance(calls, list) or not calls or len(calls) > MAX_TOOL_CALLS:
                raise _refuse(502, "invalid_response_tool")
            seen = set()
            for fragment in calls:
                if not isinstance(fragment, dict):
                    raise _refuse(502, "invalid_response_tool")
                _require_exact_keys(fragment, frozenset({"index", "id", "type", "function"}), "tool_call")
                index = fragment.get("index")
                if type(index) is not int or not (0 <= index < MAX_TOOL_CALLS) or index in seen:
                    raise _refuse(502, "invalid_response_tool")
                seen.add(index)
                fn = fragment.get("function")
                if not isinstance(fn, dict):
                    raise _refuse(502, "invalid_response_tool")
                _require_exact_keys(fn, frozenset({"name", "arguments"}), "tool_call/function")
                if index not in self.calls:
                    if index != len(self.calls) or fragment.get("type") != "function":
                        raise _refuse(502, "invalid_response_tool")
                    identity = _check_str(fragment.get("id"), "tool_call/id", MAX_ID_LEN)
                    name = _check_str(fn.get("name"), "tool_call/name", 64)
                    if not identity or name not in self.declared or any(c["id"] == identity for c in self.calls.values()):
                        raise _refuse(502, "invalid_response_tool")
                    self.calls[index] = {"id": identity, "name": name, "arguments": ""}
                call = self.calls[index]
                if ("type" in fragment and fragment["type"] != "function") or ("id" in fragment and fragment["id"] != call["id"]) or ("name" in fn and fn["name"] != call["name"]):
                    raise _refuse(502, "invalid_response_tool")
                if "arguments" in fn:
                    call["arguments"] += _check_str(fn["arguments"], "tool_call/arguments")
                    if len(call["arguments"]) > MAX_TEXT_LEN:
                        raise _refuse(502, "invalid_response_tool")
        reason = choice.get("finish_reason")
        if reason is not None:
            if reason not in ("stop", "length", "tool_calls") or bool(self.calls) != (reason == "tool_calls"):
                raise _refuse(502, "invalid_finish_reason")
            for call in self.calls.values():
                if not isinstance(_strict_json(call["arguments"], "response arguments"), dict):
                    raise _refuse(502, "invalid_response_tool")
            self.finished = True
            self.finish_reason = reason

    def nonstream(self, event: Any) -> None:
        self.envelope(event, False)
        choices = event.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise _refuse(502, "invalid_response")
        choice = choices[0]
        _require_exact_keys(choice, frozenset({"index", "message", "finish_reason", "logprobs"}), "choice")
        if type(choice.get("index")) is not int or choice["index"] != 0 or choice.get("logprobs") is not None:
            raise _refuse(502, "invalid_response")
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise _refuse(502, "invalid_response")
        _require_exact_keys(message, frozenset({"role", "content", "tool_calls", "reasoning_content", "reasoning", "reasoning_text"}), "message")
        for key in ("content", "reasoning_content", "reasoning", "reasoning_text"):
            if message.get(key) is not None:
                _check_str(message[key], "message/" + key)
        calls = message.get("tool_calls", [])
        if calls:
            _validate_tool_calls(calls, self.declared, "response/tool_calls", set())
        elif "tool_calls" in message and not isinstance(calls, list):
            raise _refuse(502, "invalid_response_tool")
        reason = choice.get("finish_reason")
        if reason not in ("stop", "length", "tool_calls") or bool(calls) != (reason == "tool_calls"):
            raise _refuse(502, "invalid_finish_reason")
        self.finished = True
        self.finish_reason = reason


class GatewayResponse:
    """Single-consumer response; raw and parsed iterators share validation.

    Text deltas stream as validated events. Function-call fragments wait for
    complete arguments, a valid finish, the stream terminator and HTTP body completion.
    The adapter closes this
    handle on disconnect, normally by using it as a context manager.
    """

    def __init__(self, conn: http.client.HTTPConnection, resp: http.client.HTTPResponse,
                 *, streaming: bool, deadline: _Deadline, chunk_size: int,
                 max_response_bytes: int, max_line_bytes: int, max_events: int,
                 model: str, declared: set) -> None:
        self._conn, self._resp, self._deadline = conn, resp, deadline
        self._streaming, self._chunk_size = streaming, chunk_size
        self._max_response_bytes, self._max_line_bytes = max_response_bytes, max_line_bytes
        self._max_events, self._total = max_events, 0
        self._closed = self._started = False
        self._state_lock = threading.Lock()
        self._reading = self._released = False
        fp = getattr(resp, "fp", None)
        self._peer = conn.sock or getattr(getattr(fp, "raw", None), "_sock", None)
        self._validator = _ChatResponseValidator(model, declared)

    @property
    def status(self) -> int:
        return self._resp.status

    @property
    def content_type(self) -> str:
        return self._resp.getheader("Content-Type", "")

    @property
    def usage(self) -> Optional[Dict[str, Any]]:
        return self._validator.usage

    @property
    def finish_reason(self) -> Optional[str]:
        return self._validator.finish_reason

    def _read(self) -> Iterator[bytes]:
        while True:
            self._deadline.check()
            with self._state_lock:
                if self._closed:
                    raise _refuse(502, "response_closed")
                self._reading = True
            try:
                chunk = self._resp.read1(self._chunk_size)
            except (http.client.HTTPException, OSError, ValueError):
                self._deadline.check()
                if self._closed:
                    raise _refuse(502, "response_closed")
                raise _refuse(502, "upstream_read_failed")
            finally:
                with self._state_lock:
                    self._reading = False
                    if self._closed:
                        self._release_locked()
            self._deadline.check()
            if self._closed:
                raise _refuse(502, "response_closed")
            if not chunk:
                if self._resp.length not in (None, 0):
                    raise _refuse(502, "upstream_body_truncated")
                return
            self._total += len(chunk)
            if self._total > self._max_response_bytes:
                raise _refuse(502, "response_too_large")
            yield chunk

    def _sse(self) -> Iterator[Optional[Dict[str, Any]]]:
        buffer = b""
        events = 0
        pending: List[Dict[str, Any]] = []
        data_lines: List[bytes] = []
        frame_bytes = 0
        emitted = 0
        done = False
        for chunk in self._read():
            if done:
                if chunk.strip(b"\r\n"):
                    raise _refuse(502, "sse_after_done")
                continue
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if len(line) > self._max_line_bytes:
                    raise _refuse(502, "sse_line_too_long")
                line = line.rstrip(b"\r")
                if line:
                    if line.startswith(b":"):
                        continue
                    if line.startswith(b"data:"):
                        data = line[5:]
                        if data.startswith(b" "):
                            data = data[1:]
                        data_lines.append(data)
                        frame_bytes += len(data)
                        if frame_bytes > self._max_line_bytes:
                            raise _refuse(502, "sse_line_too_long")
                    else:
                        raise _refuse(502, "unsupported_sse_field")
                    continue
                if not data_lines:
                    continue
                data = b"\n".join(data_lines)
                data_lines, frame_bytes = [], 0
                events += 1
                if events > self._max_events:
                    raise _refuse(502, "sse_too_many_events")
                if data == b"[DONE]":
                    if not self._validator.finished:
                        raise _refuse(502, "sse_truncated")
                    if emitted + len(self._encode_event(None)) > self._max_response_bytes:
                        raise _refuse(502, "response_too_large")
                    done = True
                    if buffer.strip(b"\r\n"):
                        raise _refuse(502, "sse_after_done")
                    buffer = b""
                    break
                try:
                    event = _strict_json(data, "SSE event")
                except GatewayError as exc:
                    raise _refuse(502, "sse_malformed", exc.code)
                self._validator.event(event)
                emitted += len(self._encode_event(event))
                if emitted > self._max_response_bytes:
                    raise _refuse(502, "response_too_large")
                if pending or self._validator.calls or self._validator.finished:
                    pending.append(event)
                else:
                    yield event
            if len(buffer) > self._max_line_bytes:
                raise _refuse(502, "sse_line_too_long")
        if not done:
            raise _refuse(502, "sse_truncated")
        yield from pending
        yield None

    def _validated(self) -> Iterator[Any]:
        with self._state_lock:
            if self._closed:
                raise _refuse(502, "response_closed")
            if self._started:
                raise _refuse(502, "response_consumed")
            self._started = True
        try:
            if self._streaming:
                for event in self._sse():
                    self._deadline.check()
                    if self._closed:
                        raise _refuse(502, "response_closed")
                    yield event
            else:
                body = b"".join(self._read())
                event = _strict_json(body, "JSON response")
                self._validator.nonstream(event)
                if len(json.dumps(event, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")) > self._max_response_bytes:
                    raise _refuse(502, "response_too_large")
                yield event
        except GatewayError as exc:
            if exc.status == 400:
                raise _refuse(502, "invalid_response", exc.code)
            raise
        finally:
            self.close()

    @staticmethod
    def _encode_event(event: Optional[Dict[str, Any]]) -> bytes:
        return b"data:[DONE]\n\n" if event is None else b"data:" + json.dumps(event, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n\n"

    def iter_bytes(self) -> Iterator[bytes]:
        for event in self._validated():
            if self._streaming:
                raw = self._encode_event(event)
            else:
                raw = json.dumps(event, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            for offset in range(0, len(raw), self._chunk_size):
                self._deadline.check()
                if self._closed:
                    raise _refuse(502, "response_closed")
                yield raw[offset:offset + self._chunk_size]

    def iter_sse(self) -> Iterator[Dict[str, Any]]:
        if not self._streaming:
            raise _refuse(400, "not_streaming")
        for event in self._validated():
            if event is not None:
                yield event

    def _release_locked(self) -> None:
        if not self._released:
            self._released = True
            try:
                self._resp.close()
            finally:
                self._conn.close()

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._deadline.cancel()
            if self._peer is not None:
                try:
                    self._peer.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            # An active reader releases the file after shutdown wakes it.
            if not self._reading:
                self._release_locked()

    def __enter__(self) -> "GatewayResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class ModelGateway:
    """One fixed literal-loopback origin, path, model and explicit bearer token."""

    PATH = "/v1/chat/completions"

    def __init__(self, origin: str, model_name: str, token: Optional[str], *,
                 deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
                 chunk_size: int = DEFAULT_CHUNK_SIZE,
                 max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
                 max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
                 max_events: int = DEFAULT_MAX_EVENTS, connect_timeout: float = 10.0) -> None:
        if not isinstance(model_name, str) or not model_name or len(model_name) > 256:
            raise _refuse(400, "invalid_model_name")
        if token is not None and (not isinstance(token, str) or not _TOKEN_RE.fullmatch(token)):
            raise _refuse(400, "invalid_token")
        for value in (deadline_seconds, connect_timeout):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 < value <= 3600) or not math.isfinite(value):
                raise _refuse(400, "invalid_timeout")
        for value, limit in ((chunk_size, DEFAULT_CHUNK_SIZE), (max_response_bytes, 256 * 1024 * 1024), (max_line_bytes, 256 * 1024 * 1024), (max_events, 1000000)):
            if type(value) is not int or not (1 <= value <= limit):
                raise _refuse(400, "invalid_limit")
        self._scheme, self._host, self._port = _parse_origin(origin)
        self.model_name, self.token = model_name, token
        self.deadline_seconds, self.connect_timeout = deadline_seconds, min(connect_timeout, deadline_seconds)
        self.chunk_size, self.max_response_bytes = chunk_size, max_response_bytes
        self.max_line_bytes, self.max_events = min(max_line_bytes, max_response_bytes), max_events

    def request(self, document: Any) -> GatewayResponse:
        payload = validate_request(document, self.model_name)
        try:
            body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError, OverflowError, RecursionError):
            raise _refuse(400, "invalid_json")
        if len(body) > MAX_BODY_BYTES:
            raise _refuse(400, "body_too_large")
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if payload["stream"] else "application/json"}
        if self.token is not None:
            headers["Authorization"] = "Bearer " + self.token
        if self._scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(self._host, self._port, timeout=self.connect_timeout, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(self._host, self._port, timeout=self.connect_timeout)
        # HTTP(S)Connection otherwise calls getaddrinfo even for numeric hosts.
        conn._create_connection = _connect_literal
        conn.response_class = _FramedHTTPResponse
        deadline = _Deadline(conn, self.deadline_seconds)
        resp = None
        try:
            conn.request("POST", self.PATH, body=body, headers=headers)
            resp = conn.getresponse()
            deadline.response = resp
            deadline.check()
            _check_upstream_headers(resp)
            if resp.status != 200:
                raise _refuse(502, "upstream_status")
            content_type = resp.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != ("text/event-stream" if payload["stream"] else "application/json"):
                raise _refuse(502, "content_type")
            return GatewayResponse(conn, resp, streaming=payload["stream"], deadline=deadline,
                                   chunk_size=self.chunk_size, max_response_bytes=self.max_response_bytes,
                                   max_line_bytes=self.max_line_bytes, max_events=self.max_events,
                                   model=self.model_name, declared={t["function"]["name"] for t in payload.get("tools", [])})
        except (GatewayError, http.client.HTTPException, OSError) as exc:
            deadline.cancel()
            if resp is not None:
                resp.close()
            conn.close()
            deadline.check()
            if isinstance(exc, GatewayError):
                raise
            raise _refuse(502, "upstream_unavailable")

    def __enter__(self) -> "ModelGateway":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None
