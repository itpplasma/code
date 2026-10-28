"""Bounded Responses transport; the caller owns policy, credentials and confinement.

Only text and guest-executed function/custom tools cross this adapter. Raw HTTP
framing, duplicate request keys and guest identity belong to the calling broker.
"""

import http.client
import ipaddress
import json
import math
import copy
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit


MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 512 * 1024
MAX_STRING = 256 * 1024
MAX_DEPTH = 32
MAX_NODES = 40000
MAX_ITEMS = 2048
MAX_EVENTS = 50000
MAX_HTTP_HEADER_BYTES = 32 * 1024
MAX_HTTP_HEADER_LINE = 8 * 1024
MAX_HTTP_HEADER_FIELDS = 64
READ_CHUNK = 16384
IO_TIMEOUT = 10.0
STREAM_DEADLINE = 120.0
_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_RESOLVERS = threading.BoundedSemaphore(4)
_OPERATIONS = threading.BoundedSemaphore(32)


class GatewayError(Exception):
    def __init__(self, status, code):
        self.status = status
        self.code = code
        super().__init__(code)


def _need(condition):
    if not condition:
        raise GatewayError(400, "invalid_request")


def _obj(value, keys, required=()):
    _need(isinstance(value, dict) and set(value) <= set(keys)
          and set(required) <= set(value))


def _string(value, limit=MAX_STRING, nonempty=False):
    _need(isinstance(value, str) and len(value) <= limit
          and (bool(value) or not nonempty))
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise GatewayError(400, "invalid_request") from None


def _integer(value, low=0, high=131072):
    _need(type(value) is int and low <= value <= high)


def _bool(value):
    _need(type(value) is bool)


def _array(value, limit=MAX_ITEMS):
    _need(isinstance(value, list) and len(value) <= limit)


def _bounded(value):
    nodes = [0]

    def visit(item, depth):
        nodes[0] += 1
        _need(depth <= MAX_DEPTH and nodes[0] <= MAX_NODES)
        if isinstance(item, dict):
            _need(len(item) <= 256)
            for key, child in item.items():
                _string(key, 128)
                visit(child, depth + 1)
        elif isinstance(item, list):
            _array(item)
            for child in item:
                visit(child, depth + 1)
        elif isinstance(item, str):
            _string(item)
        else:
            _need(item is None or type(item) in (bool, int, float))
            if type(item) in (int, float):
                _need(abs(item) <= 10 ** 15 and math.isfinite(item))

    visit(value, 0)


def _schema(schema, root=None):
    if type(schema) is bool:
        return
    _obj(schema, ("type", "properties", "required", "additionalProperties",
                  "items", "enum", "const", "anyOf", "oneOf", "allOf", "$defs",
                  "$ref", "title", "description", "default", "examples",
                  "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                  "minLength", "maxLength", "minItems", "maxItems", "pattern"))
    if root is None:
        root = schema
    if "type" in schema:
        kinds = schema["type"]
        if isinstance(kinds, str):
            kinds = [kinds]
        _array(kinds, 7)
        _need(bool(kinds) and all(isinstance(kind, str) and kind in
              ("object", "array", "string", "number", "integer", "boolean", "null")
              for kind in kinds))
    for key in ("title", "description", "pattern"):
        if key in schema:
            _string(schema[key])
    for key in ("properties", "$defs"):
        if key in schema:
            _need(isinstance(schema[key], dict) and len(schema[key]) <= 128)
            for name, child in schema[key].items():
                _string(name, 128, True)
                _schema(child, root)
    if "$ref" in schema:
        ref = schema["$ref"]
        _string(ref, 256)
        _need(re.fullmatch(r"#/\$defs/[A-Za-z0-9_-]+", ref) is not None)
        _need(ref[8:] in root.get("$defs", {}))
    if "required" in schema:
        _array(schema["required"], 128)
        _need(all(isinstance(name, str) and name in schema.get("properties", {})
                  for name in schema["required"]))
        _need(len(set(schema["required"])) == len(schema["required"]))
    for key in ("items", "additionalProperties"):
        if key in schema:
            _schema(schema[key], root)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            _array(schema[key], 32)
            _need(bool(schema[key]))
            for child in schema[key]:
                _schema(child, root)
    for key in ("enum", "examples"):
        if key in schema:
            _array(schema[key], 256)
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema:
            _integer(schema[key], high=MAX_STRING)
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if key in schema:
            _need(type(schema[key]) in (int, float))


def _tools(tools):
    _array(tools, 64)
    names = {}
    for tool in tools:
        _need(isinstance(tool, dict))
        kind = tool.get("type")
        _need(kind in ("function", "custom"))
        _obj(tool, ("type", "name", "description", "strict", "parameters")
             if kind == "function" else ("type", "name", "description", "format"),
             ("type", "name"))
        name = tool["name"]
        _need(isinstance(name, str) and _NAME.fullmatch(name) is not None
              and name not in names)
        names[name] = kind
        if "description" in tool:
            _string(tool["description"])
        if kind == "function":
            _need("parameters" in tool)
            _schema(tool["parameters"])
            if "strict" in tool:
                _bool(tool["strict"])
        elif "format" in tool:
            fmt = tool["format"]
            _obj(fmt, ("type", "syntax", "definition"), ("type",))
            if fmt["type"] == "text":
                _need(set(fmt) == {"type"})
            else:
                _need(fmt["type"] == "grammar" and set(fmt) ==
                      {"type", "syntax", "definition"})
                _need(fmt["syntax"] in ("lark", "regex"))
                _string(fmt["definition"], nonempty=True)
    return names


def _choice(value, names):
    if isinstance(value, str):
        _need(value in ("auto", "none", "required"))
        _need(value != "required" or bool(names))
    else:
        _obj(value, ("type", "name"), ("type", "name"))
        _need(value["type"] in ("function", "custom")
              and isinstance(value["name"], str)
              and names.get(value["name"]) == value["type"])


def _text_part(part, kinds=("input_text", "output_text")):
    _obj(part, ("type", "text", "annotations", "logprobs"), ("type", "text"))
    _need(part["type"] in kinds)
    _string(part["text"])
    for key in ("annotations", "logprobs"):
        if key in part:
            _need(part[key] == [])


def _content(content, kinds=("input_text", "output_text")):
    if isinstance(content, str):
        _string(content)
    else:
        _array(content)
        for part in content:
            _text_part(part, kinds)


def _arguments(value):
    _string(value)
    _need(len(value.encode("utf-8")) <= MAX_STRING)
    try:
        decoded = _json(value)
    except GatewayError:
        raise GatewayError(400, "invalid_request") from None
    _need(isinstance(decoded, dict))
    return decoded


def _item(item, names, output=False, partial=False):
    _need(isinstance(item, dict))
    _need(not output or ("type" in item and "id" in item))
    kind = item.get("type", "message")
    if kind == "message":
        _obj(item, ("type", "id", "role", "content", "status", "phase"),
             ("role", "content"))
        _need(item["role"] in ("system", "developer", "user", "assistant"))
        _need(not output or item["role"] == "assistant")
        _content(item["content"])
        if "phase" in item:
            _need(item["phase"] in ("commentary", "final_answer"))
    elif kind in ("function_call", "custom_tool_call"):
        field = "arguments" if kind == "function_call" else "input"
        _obj(item, ("type", "id", "call_id", "name", field, "status"),
             ("type", "call_id", "name", field))
        _need(isinstance(item["name"], str) and names.get(item["name"]) ==
              ("function" if kind == "function_call" else "custom"))
        _string(item["call_id"], 256, True)
        _string(item[field])
        if kind == "function_call" and not partial:
            _arguments(item[field])
    elif kind in ("function_call_output", "custom_tool_call_output"):
        _need(not output)
        _obj(item, ("type", "id", "call_id", "output", "status"),
             ("type", "call_id", "output"))
        _string(item["call_id"], 256, True)
        _content(item["output"], ("input_text",))
    elif kind == "reasoning":
        _obj(item, ("type", "id", "summary", "content", "encrypted_content", "status"),
             ("type", "summary"))
        for key, part_kind in (("summary", "summary_text"),
                               ("content", "reasoning_text")):
            if key in item and not (key == "content" and item[key] is None):
                _array(item[key])
                for part in item[key]:
                    _text_part(part, (part_kind,))
        if "encrypted_content" in item:
            _string(item["encrypted_content"])
    else:
        _need(False)
    if "id" in item:
        _string(item["id"], 256, True)
    if "status" in item:
        _need(item["status"] in ("in_progress", "completed", "incomplete"))


def _reasoning(value):
    _obj(value, ("effort", "summary"))
    if "effort" in value:
        _need(value["effort"] in
              ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"))
    if "summary" in value:
        _need(value["summary"] in ("none", "auto", "concise", "detailed"))


def _text(value):
    _obj(value, ("format", "verbosity"))
    if "verbosity" in value:
        _need(value["verbosity"] in ("low", "medium", "high"))
    if "format" in value:
        fmt = value["format"]
        _obj(fmt, ("type", "name", "description", "schema", "strict"), ("type",))
        if fmt["type"] == "text":
            _need(set(fmt) == {"type"})
        else:
            _need(fmt["type"] == "json_schema" and "schema" in fmt and "name" in fmt)
            _string(fmt["name"], 128, True)
            _schema(fmt["schema"])
            if "description" in fmt:
                _string(fmt["description"])
            if "strict" in fmt:
                _bool(fmt["strict"])


def validate_request(document, fixed_model):
    """Validate a decoded request and return a detached, fixed-model document."""
    _need(isinstance(fixed_model, str) and _MODEL.fullmatch(fixed_model) is not None)
    _bounded(document)
    _obj(document, ("model", "stream", "input", "instructions", "tools", "tool_choice",
                    "parallel_tool_calls", "reasoning", "store", "include", "text",
                    "max_output_tokens", "temperature", "top_p", "prompt_cache_key",
                    "client_metadata"), ("input",))
    _need(document.get("model", fixed_model) == fixed_model)
    _need(document.get("stream", True) is True and document.get("store", False) is False)
    names = _tools(document.get("tools", []))
    content = document["input"]
    if isinstance(content, str):
        _string(content)
    else:
        _array(content)
        pending, calls, identifiers, consuming = {}, set(), set(), False
        for item in content:
            _item(item, names)
            if "id" in item:
                _need(item["id"] not in identifiers)
                identifiers.add(item["id"])
            kind = item.get("type", "message")
            if kind in ("function_call", "custom_tool_call"):
                _need(not consuming and item["call_id"] not in calls)
                calls.add(item["call_id"])
                pending[item["call_id"]] = kind
            elif kind in ("function_call_output", "custom_tool_call_output"):
                call_id = item["call_id"]
                _need(pending.get(call_id) == kind[:-7])
                del pending[call_id]
                consuming = bool(pending)
            else:
                _need(not pending)
        _need(not pending)
    for key in ("instructions", "prompt_cache_key"):
        if key in document:
            _string(document[key], 256 if key == "prompt_cache_key" else MAX_STRING)
    if "parallel_tool_calls" in document:
        _bool(document["parallel_tool_calls"])
    if "tool_choice" in document:
        _choice(document["tool_choice"], names)
    if "reasoning" in document:
        _reasoning(document["reasoning"])
    if "text" in document:
        _text(document["text"])
    if "include" in document:
        _need(document["include"] in ([], ["reasoning.encrypted_content"]))
    if "max_output_tokens" in document:
        _integer(document["max_output_tokens"], 1)
    for key, limit in (("temperature", 2), ("top_p", 1)):
        if key in document:
            _need(type(document[key]) in (float, int) and 0 <= document[key] <= limit)
    if "client_metadata" in document:
        metadata = document["client_metadata"]
        _obj(metadata, ("x-codex-installation-id", "x-codex-turn-metadata", "x-codex-window-id",
                        "thread_id", "session_id", "turn_id", "root_turn_id"))
        for value in metadata.values():
            _string(value, 8192)
    result = dict(document, model=fixed_model, stream=True, store=False)
    try:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except UnicodeError:
        raise GatewayError(400, "invalid_request") from None
    if len(encoded) > MAX_REQUEST_BYTES:
        raise GatewayError(413, "request_too_large")
    return json.loads(encoded)


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result)
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=pairs,
                           parse_constant=lambda _: _need(False))
        _bounded(value)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise GatewayError(502, "upstream_invalid") from None


def _usage(value):
    _obj(value, ("input_tokens", "output_tokens", "total_tokens", "input_tokens_details",
                 "output_tokens_details"))
    for key, count in value.items():
        if key.endswith("_details"):
            _obj(count, ("cached_tokens",) if key == "input_tokens_details"
                 else ("reasoning_tokens",))
            for detail in count.values():
                _integer(detail, high=10 ** 12)
        else:
            _integer(count, high=10 ** 12)


def _response(value, names, model):
    _obj(value, ("id", "object", "created_at", "status", "error", "incomplete_details",
                 "instructions", "max_output_tokens", "model", "output", "parallel_tool_calls",
                 "previous_response_id", "reasoning", "store", "temperature", "text",
                 "tool_choice", "tools", "top_p", "truncation", "usage", "metadata",
                 "service_tier", "background", "conversation", "prompt_cache_key"),
         ("id", "status", "output"))
    _string(value["id"], 256, True)
    _need(value["status"] in ("in_progress", "completed", "failed", "incomplete", "cancelled"))
    _need(value.get("model", model) == model and value.get("store", False) is False
          and value.get("background", False) is False
          and value.get("previous_response_id") is None and value.get("conversation") is None)
    _array(value["output"])
    for item in value["output"]:
        _item(item, names, output=True, partial=value["status"] != "completed")
    if "tools" in value:
        _need(_tools(value["tools"]) == names)
    if "tool_choice" in value:
        _choice(value["tool_choice"], names)
    if "reasoning" in value and value["reasoning"] is not None:
        _reasoning(value["reasoning"])
    if "text" in value:
        _text(value["text"])
    if "usage" in value and value["usage"] is not None:
        _usage(value["usage"])
    if "error" in value and value["error"] is not None:
        _obj(value["error"], ("code", "message"), ("code", "message"))
        for text in value["error"].values():
            _string(text)
    if "incomplete_details" in value and value["incomplete_details"] is not None:
        _obj(value["incomplete_details"], ("reason",), ("reason",))
        _string(value["incomplete_details"]["reason"], 256)
    if "metadata" in value:
        _need(value["metadata"] == {})
    for key in ("instructions", "prompt_cache_key"):
        if key in value and value[key] is not None:
            _string(value[key])
    for key in ("created_at", "max_output_tokens"):
        if key in value and value[key] is not None:
            _integer(value[key], high=10 ** 12)
    if "parallel_tool_calls" in value:
        _bool(value["parallel_tool_calls"])
    for key in ("temperature", "top_p"):
        if key in value:
            _need(value[key] is None or type(value[key]) in (int, float))
    if "object" in value:
        _need(value["object"] == "response")
    if "truncation" in value:
        _need(value["truncation"] in ("disabled", "auto"))
    if "service_tier" in value:
        _need(value["service_tier"] in (None, "auto", "default", "priority", "flex", "scale"))


_EVENTS = frozenset((
    "response.created", "response.in_progress", "response.completed", "response.failed",
    "response.incomplete", "error", "response.output_item.added", "response.output_item.done",
    "response.content_part.added", "response.content_part.done", "response.output_text.delta",
    "response.output_text.done", "response.function_call_arguments.delta",
    "response.function_call_arguments.done", "response.custom_tool_call_input.delta",
    "response.custom_tool_call_input.done", "response.reasoning_summary_part.added",
    "response.reasoning_summary_part.done", "response.reasoning_summary_text.delta",
    "response.reasoning_summary_text.done", "response.reasoning_text.delta",
    "response.reasoning_text.done"))
_TERMINAL = frozenset(("response.completed", "response.failed", "response.incomplete", "error"))


def _event(raw, names, model):
    event_name, data = None, []
    for line in raw.decode("utf-8").split("\n"):
        line = line.removesuffix("\r")
        if not line or line.startswith(":"):
            continue
        field, colon, value = line.partition(":")
        _need(bool(colon) and field in ("event", "data"))
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            _need(event_name is None)
            event_name = value
        else:
            data.append(value)
    if not data:
        _need(event_name is None)
        return None, None
    if "\n".join(data) == "[DONE]":
        return "done", None
    value = _json("\n".join(data))
    _obj(value, ("type", "sequence_number", "response", "output_index", "item", "item_id",
                 "content_index", "summary_index", "part", "delta", "text", "arguments",
                 "input", "name", "logprobs", "obfuscation", "code", "message", "param"),
         ("type",))
    kind = value["type"]
    _need(isinstance(kind, str) and kind in _EVENTS
          and (event_name is None or event_name == kind))
    if kind in ("response.created", "response.in_progress") or kind in _TERMINAL - {"error"}:
        fields = required = ("response",)
    elif kind == "error":
        fields, required = ("code", "message", "param"), ("code", "message")
    elif kind.startswith("response.output_item."):
        fields = required = ("output_index", "item")
    elif kind.startswith("response.content_part."):
        fields = required = ("output_index", "item_id", "content_index", "part")
    elif kind.startswith("response.reasoning_summary_part."):
        fields = required = ("output_index", "item_id", "summary_index", "part")
    else:
        index = "summary_index" if ".reasoning_summary_text." in kind else "content_index"
        if ".function_call_arguments." in kind or ".custom_tool_call_input." in kind:
            required = ("output_index", "item_id")
        else:
            required = ("output_index", "item_id", index)
        payload = "delta" if kind.endswith(".delta") else (
            "arguments" if ".function_call_arguments." in kind else
            "input" if ".custom_tool_call_input." in kind else "text")
        required += (payload,)
        fields = required + ("obfuscation", "logprobs", "name")
    _obj(value, fields + ("type", "sequence_number"), required)
    if "name" in value:
        _need(isinstance(value["name"], str) and value["name"] in names)
    for key in ("sequence_number", "output_index", "content_index", "summary_index"):
        if key in value:
            _integer(value[key], high=10 ** 12)
    for key in ("item_id", "delta", "text", "arguments", "input", "name", "obfuscation",
                "code", "message", "param"):
        if key in value and value[key] is not None:
            _string(value[key])
    if "logprobs" in value:
        _need(value["logprobs"] == [])
    if "response" in value:
        _response(value["response"], names, model)
    if kind in ("response.created", "response.in_progress") or kind in _TERMINAL - {"error"}:
        _need("response" in value)
        expected = "in_progress" if kind == "response.created" else kind.split(".")[1]
        _need(value["response"]["status"] == expected)
    if "item" in value:
        _item(value["item"], names, output=True, partial=kind == "response.output_item.added")
    if kind.startswith("response.output_item."):
        _need("item" in value and "output_index" in value)
    if "part" in value:
        _text_part(value["part"], ("output_text", "summary_text", "reasoning_text"))
    return kind, value


class _Sequence:
    def __init__(self, history=()):
        self.started = False
        self.terminal = False
        self.done = False
        self.progress = False
        self.response_id = None
        self.sequence = -1
        self.items = {}
        self.identifiers = {item["id"] for item in history if "id" in item}
        self.calls = {item["call_id"] for item in history
                      if item.get("type") in ("function_call", "custom_tool_call")}

    def _lookup(self, value):
        index = value["output_index"]
        _need(index in self.items)
        state = self.items[index]
        _need(not state["done"] and value["item_id"] == state["item"]["id"])
        return state

    @staticmethod
    def _append(state, field, delta):
        _need(isinstance(delta, str))
        combined = state[field] + delta
        _need(len(combined.encode("utf-8")) <= MAX_STRING)
        state[field] = combined

    @staticmethod
    def _match_item(state, item, finished):
        original = state["item"]
        _need(item["id"] == original["id"] and item["type"] == original["type"])
        if item["type"] in ("function_call", "custom_tool_call"):
            field = "arguments" if item["type"] == "function_call" else "input"
            _need((not finished or state["argument_done"]) and item[field] == state["argument"]
                  and item["name"] == original["name"] and item["call_id"] == original["call_id"])
        else:
            _need(not finished or all(part["done"] for part in state["parts"].values()))
            for field in (("content",) if item["type"] == "message" else ("summary", "content")):
                parts = [part["part"] for key, part in sorted(state["parts"].items()) if key[0] == field]
                actual = item.get(field)
                _need(actual == parts or (field == "content" and actual is None and not parts))
            if item["type"] == "message":
                _need(item["role"] == original["role"])

    def accept(self, kind, value):
        if kind is None:
            return
        if kind == "done":
            _need(self.terminal and not self.done)
            self.done = True
            return
        _need(not self.terminal and not self.done)
        if "sequence_number" in value:
            _need(value["sequence_number"] > self.sequence)
            self.sequence = value["sequence_number"]
        if kind == "response.created":
            _need(not self.started and value["response"]["output"] == [])
            self.started = True
            self.response_id = value["response"]["id"]
            return
        if kind == "error":
            self.terminal = True
            return
        _need(self.started)
        if "response" in value:
            _need(value["response"]["id"] == self.response_id)
        if kind == "response.in_progress":
            _need(not self.progress and not self.items and value["response"]["output"] == [])
            self.progress = True
            return
        if kind in _TERMINAL:
            output = value["response"]["output"]
            _need(len(output) == len(self.items))
            for index, item in enumerate(output):
                state = self.items[index]
                if state["done"]:
                    _need(item == state["final"])
                else:
                    _need(kind != "response.completed")
                    self._match_item(state, item, finished=False)
                if kind == "response.completed":
                    _need(item.get("status", "completed") == "completed")
            self.terminal = True
            return
        if kind == "response.output_item.added":
            index, item = value["output_index"], value["item"]
            _need(index == len(self.items) and index < MAX_ITEMS and "id" in item
                  and item["id"] not in self.identifiers
                  and item.get("status", "in_progress") == "in_progress")
            self.identifiers.add(item["id"])
            state = {"item": copy.deepcopy(item), "done": False,
                     "parts": {}, "argument_done": False}
            if item["type"] in ("function_call", "custom_tool_call"):
                _need(item["call_id"] not in self.calls)
                self.calls.add(item["call_id"])
                state["argument"] = item["arguments"] if item["type"] == "function_call" else item["input"]
            elif item["type"] == "message":
                _need(item["content"] == [])
            else:
                _need(item["summary"] == [] and item.get("content") in (None, []))
            self.items[index] = state
            return
        if kind == "response.output_item.done":
            index, item = value["output_index"], value["item"]
            state = self._lookup({"output_index": index, "item_id": item.get("id")})
            self._match_item(state, item, finished=True)
            _need(item.get("status", "completed") in ("completed", "incomplete"))
            state["done"], state["final"] = True, copy.deepcopy(item)
            return
        state = self._lookup(value)
        item_kind = state["item"]["type"]
        if ".function_call_arguments." in kind or ".custom_tool_call_input." in kind:
            expected_kind = "function_call" if ".function_call_arguments." in kind else "custom_tool_call"
            _need(item_kind == expected_kind and not state["argument_done"])
            if "name" in value:
                _need(value["name"] == state["item"]["name"])
            if kind.endswith(".delta"):
                self._append(state, "argument", value["delta"])
            else:
                field = "arguments" if item_kind == "function_call" else "input"
                _need(value[field] == state["argument"])
                if item_kind == "function_call":
                    _arguments(state["argument"])
                state["argument_done"] = True
            return
        summary = ".reasoning_summary_" in kind
        reasoning = (".reasoning_text." in kind or summary
                     or (".content_part." in kind and item_kind == "reasoning"))
        _need(item_kind == ("reasoning" if reasoning else "message"))
        field = "summary" if summary else "content"
        index = value["summary_index" if summary else "content_index"]
        key = field, index
        if kind.endswith("_part.added"):
            _need(key not in state["parts"]
                  and index == sum(1 for existing in state["parts"] if existing[0] == field))
            part = copy.deepcopy(value["part"])
            _need(part["type"] == ("summary_text" if summary else
                                   "reasoning_text" if reasoning else "output_text"))
            state["parts"][key] = {"part": part, "text": part["text"], "text_done": False, "done": False}
            return
        _need(key in state["parts"])
        part = state["parts"][key]
        _need(not part["done"])
        if kind.endswith("_part.done"):
            _need(part["text_done"] and value["part"] == part["part"])
            part["done"] = True
        elif kind.endswith(".delta"):
            _need(not part["text_done"])
            self._append(part, "text", value["delta"])
            part["part"]["text"] = part["text"]
        else:
            _need(not part["text_done"] and value["text"] == part["text"])
            part["text_done"] = True


class _Operation:
    """Own sockets from creation, so expiration and cancellation unblock reads."""

    def __init__(self, started, caller_deadline=None):
        self._slots = _OPERATIONS
        if not self._slots.acquire(blocking=False):
            raise GatewayError(503, "gateway_busy")
        self.final_deadline = started + STREAM_DEADLINE
        if caller_deadline is not None:
            self.final_deadline = min(self.final_deadline, caller_deadline)
        self.deadline = min(self.final_deadline, started + IO_TIMEOUT)
        self.socket = None
        self.expired = False
        self.closed = False
        self.condition = threading.Condition()
        try:
            threading.Thread(target=self._watch, daemon=True, name="codex-deadline").start()
        except RuntimeError:
            self._slots.release()
            raise GatewayError(503, "gateway_busy") from None

    @staticmethod
    def _shutdown(sock):
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _watch(self):
        try:
            with self.condition:
                while not self.closed:
                    remaining = self.deadline - time.monotonic()
                    if remaining <= 0:
                        self.expired = True
                        sock = self.socket
                        break
                    self.condition.wait(remaining)
                else:
                    return
            self._shutdown(sock)
        finally:
            self._slots.release()

    def expire(self):
        with self.condition:
            if self.closed:
                return
            self.expired = True
            self.deadline = min(self.deadline, time.monotonic())
            sock = self.socket
            self.condition.notify_all()
        self._shutdown(sock)

    def remaining(self):
        with self.condition:
            remaining = self.deadline - time.monotonic()
            if self.closed or self.expired or remaining <= 0:
                self.expired = self.expired or remaining <= 0
                raise GatewayError(504 if self.expired else 502,
                                   "upstream_timeout" if self.expired else "response_closed")
            return min(IO_TIMEOUT, remaining)

    def attach(self, sock):
        with self.condition:
            if self.closed or self.expired or time.monotonic() >= self.deadline:
                self._shutdown(sock)
                raise GatewayError(504, "upstream_timeout")
            self.socket = sock

    def body_phase(self):
        self.remaining()
        with self.condition:
            self.deadline = self.final_deadline
            self.condition.notify_all()
        self.remaining()

    def close(self):
        with self.condition:
            if self.closed:
                return
            self.closed = True
            sock = self.socket
            self.condition.notify_all()
        self._shutdown(sock)


def _resolve(host, port, operation):
    slots = _RESOLVERS
    if not slots.acquire(blocking=False):
        raise GatewayError(503, "resolver_busy")
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            result.put((socket.getaddrinfo(host, port, type=socket.SOCK_STREAM), None))
        except Exception as error:
            result.put((None, error))
        finally:
            with operation.condition:
                operation.condition.notify_all()
            slots.release()

    try:
        threading.Thread(target=resolve, daemon=True, name="codex-resolver").start()
    except RuntimeError:
        slots.release()
        raise GatewayError(503, "resolver_busy") from None
    with operation.condition:
        while result.empty():
            operation.condition.wait(operation.remaining())
        addresses, error = result.get_nowait()
    operation.remaining()
    if error is not None:
        raise GatewayError(502, "upstream_io")
    _need(isinstance(addresses, list) and 0 < len(addresses) <= 64)
    return addresses


class _FramedHTTPResponse(http.client.HTTPResponse):
    def begin(self):
        super().begin()
        lengths = [value.strip(" \t") for value in self.headers.get_all("Content-Length", [])]
        transfers = [value.strip(" \t") for value in self.headers.get_all("Transfer-Encoding", [])]
        fields = list(self.headers.items())
        if len(transfers) == 1 and transfers[0].lower() == "chunked" and not lengths:
            # The stdlib's chunked discriminator does not trim legal trailing whitespace.
            self.chunked, self.chunk_left, self.length = True, None, None
            self.will_close = self._check_close()
        if (len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers)
                or len(fields) > MAX_HTTP_HEADER_FIELDS
                or sum(len(key) + len(value) + 4 for key, value in fields) > MAX_HTTP_HEADER_BYTES
                or any(len(key) + len(value) + 4 > MAX_HTTP_HEADER_LINE for key, value in fields)
                or (lengths and (re.fullmatch(r"[0-9]+", lengths[0]) is None or self.length is None))
                or (transfers and (transfers[0].lower() != "chunked" or not self.chunked))):
            raise GatewayError(502, "upstream_invalid_headers")

    def _get_chunk_left(self):
        if not self.chunk_left:
            if self.chunk_left is not None and self._safe_read(2) != b"\r\n":
                raise http.client.IncompleteRead(b"")
            line = self.fp.readline(MAX_HTTP_HEADER_LINE + 1)
            size = line[:-2].partition(b";")[0]
            if (not line.endswith(b"\r\n") or len(line) > MAX_HTTP_HEADER_LINE
                    or re.fullmatch(rb"[0-9a-fA-F]{1,16}", size) is None):
                raise http.client.IncompleteRead(b"")
            self.chunk_left = int(size, 16)
            if self.chunk_left == 0:
                self._read_and_discard_trailer()
                self._close_conn()
                self.chunk_left = None
        return self.chunk_left

    def _read_and_discard_trailer(self):
        # The stdlib tolerates trailer EOF; inference completion requires its blank line.
        total = 0
        for _ in range(MAX_HTTP_HEADER_FIELDS + 1):
            line = self.fp.readline(MAX_HTTP_HEADER_LINE + 1)
            total += len(line)
            if (not line.endswith(b"\r\n") or len(line) > MAX_HTTP_HEADER_LINE
                    or total > MAX_HTTP_HEADER_BYTES):
                raise http.client.IncompleteRead(b"")
            if line == b"\r\n":
                return
            if re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+:[^\r\n]*\r\n", line) is None:
                raise http.client.IncompleteRead(b"")
        raise http.client.IncompleteRead(b"")


class _FixedConnection(http.client.HTTPConnection):
    response_class = _FramedHTTPResponse

    def __init__(self, host, port, scheme, operation, addresses):
        super().__init__(host, port, timeout=operation.remaining())
        self._scheme = scheme
        self._operation = operation
        self._addresses = addresses

    def connect(self):
        last_error = None
        for family, kind, protocol, _, address in self._addresses:
            self._operation.remaining()
            sock = socket.socket(family, kind, protocol)
            self._operation.attach(sock)
            try:
                sock.settimeout(self._operation.remaining())
                sock.connect(address)
                if self._scheme == "https":
                    context = ssl.create_default_context()
                    self._operation.remaining()
                    sock = context.wrap_socket(sock, server_hostname=self.host,
                                               do_handshake_on_connect=False)
                    self._operation.attach(sock)
                    sock.settimeout(self._operation.remaining())
                    sock.do_handshake()
                self._operation.remaining()
                self.sock = sock
                return
            except OSError as error:
                last_error = error
                sock.close()
                self._operation.remaining()
        raise last_error or OSError("no upstream address")


class GatewayResponse:
    def __init__(self, status, content_type, body=None, upstream=None,
                 connection=None, transport_socket=None, operation=None,
                 names=None, model=None, headers=None, history=(), expected_length=None):
        self.status = status
        self.content_type = content_type
        self.headers = headers or {}
        self._body = body
        self._upstream = upstream
        self._connection = connection
        self._socket = transport_socket
        self._operation = operation
        self._names = names or {}
        self._model = model
        self._history = history
        self._expected_length = expected_length
        self._closed = False
        self._started = False
        self._reader_lock = threading.RLock()
        self._deadline = time.monotonic() + STREAM_DEADLINE

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._closed = True
        if self._operation is not None:
            self._operation.close()
        with self._reader_lock:
            if self._upstream is not None:
                self._upstream.close()
            if self._connection is not None:
                self._connection.close()

    def iter_bytes(self):
        if self._closed or self._started:
            raise GatewayError(502, "response_closed")
        self._started = True
        try:
            if self._body is not None:
                yield self._body
                return
            pending, total, events, scan = b"", 0, 0, 0
            sequence = _Sequence(self._history)
            terminal_chunks = []
            while not self._closed:
                remaining = (self._operation.remaining() if self._operation is not None
                             else self._deadline - time.monotonic())
                if remaining <= 0:
                    raise GatewayError(504, "upstream_timeout")
                with self._reader_lock:
                    if self._closed:
                        return
                    if not self._upstream.isclosed() and self._socket is not None:
                        self._socket.settimeout(min(IO_TIMEOUT, remaining))
                    chunk = self._upstream.read1(READ_CHUNK)
                if self._operation is not None:
                    self._operation.remaining()
                if not chunk:
                    if (pending or not sequence.terminal
                            or (self._expected_length is not None and total != self._expected_length)):
                        raise GatewayError(502, "upstream_truncated")
                    for terminal_chunk in terminal_chunks:
                        if self._operation is not None:
                            self._operation.remaining()
                        yield terminal_chunk
                    return
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise GatewayError(502, "upstream_limit")
                pending += chunk
                while True:
                    if self._operation is not None:
                        self._operation.remaining()
                    match = re.compile(br"\r?\n\r?\n").search(pending, scan)
                    if match is None:
                        if len(pending) > MAX_EVENT_BYTES:
                            raise GatewayError(502, "upstream_limit")
                        scan = max(0, len(pending) - 3)
                        break
                    raw, pending = pending[:match.end()], pending[match.end():]
                    scan = 0
                    events += 1
                    if len(raw) > MAX_EVENT_BYTES or events > MAX_EVENTS:
                        raise GatewayError(502, "upstream_limit")
                    try:
                        kind, value = _event(raw, self._names, self._model)
                        sequence.accept(kind, value)
                    except (GatewayError, ValueError, UnicodeError, TypeError, RecursionError):
                        raise GatewayError(502, "upstream_invalid") from None
                    if self._operation is not None:
                        self._operation.remaining()
                    if sequence.terminal:
                        terminal_chunks.append(raw)
                    else:
                        yield raw
        except (socket.timeout, TimeoutError):
            raise GatewayError(504, "upstream_timeout") from None
        except (OSError, http.client.HTTPException):
            if self._operation is not None and self._operation.expired:
                raise GatewayError(504, "upstream_timeout") from None
            raise GatewayError(502, "upstream_io") from None
        finally:
            self.close()


def _response_headers(upstream):
    headers = {}
    for key, value in upstream.getheaders():
        if key.lower() == "retry-after" or key.lower().startswith("x-ratelimit-"):
            if (len(headers) < 32 and len(key) <= 128 and len(value) <= 512
                    and all(32 <= ord(ch) < 127 for ch in key + value)):
                headers[key.lower()] = value
    return headers


class CodexGateway:
    def __init__(self, origin, model, token, account_id=None):
        """Accept only caller-fixed origin/credentials; never read environment auth."""
        try:
            parsed = urlsplit(origin)
            _need(parsed.scheme in ("http", "https") and parsed.hostname
                  and not parsed.username and not parsed.password
                  and not parsed.query and not parsed.fragment
                  and parsed.path in ("", "/", "/v1", "/backend-api/codex")
                  and not any(ch.isspace() for ch in origin))
            if parsed.scheme == "http":
                _need(ipaddress.ip_address(parsed.hostname).is_loopback)
            self._port = parsed.port
            _need(isinstance(model, str) and _MODEL.fullmatch(model) is not None)
            for value, limit in ((token, 16384), (account_id, 512)):
                if value is not None:
                    _string(value, limit, True)
                    _need(all(32 <= ord(ch) < 127 for ch in value))
            _need(token is not None)
        except (GatewayError, ValueError, TypeError):
            raise GatewayError(500, "invalid_gateway_config") from None
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._prefix = parsed.path.rstrip("/") or "/v1"
        self._model = model
        self._token = token
        self._account = account_id

    def request(self, path, document=None, *, deadline=None, register_cancel=None):
        """Optional deadline/cancellation hooks are trusted caller authority only."""
        started = time.monotonic()
        if ((deadline is not None and (type(deadline) not in (int, float)
                                      or abs(deadline) > 10 ** 15
                                      or not math.isfinite(deadline)))
                or (register_cancel is not None and not callable(register_cancel))):
            raise GatewayError(500, "invalid_gateway_call")
        if deadline is not None and deadline <= started:
            raise GatewayError(504, "upstream_timeout")
        if path == "/v1/models":
            _need(document is None)
            body = json.dumps({"object": "list", "data": [{"id": self._model,
                               "object": "model", "owned_by": "configured-provider"}]}).encode()
            return GatewayResponse(200, "application/json", body=body)
        if path != "/v1/responses":
            raise GatewayError(404, "route_denied")
        validated = validate_request(document, self._model)
        body = json.dumps(validated, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers = {"Authorization": "Bearer " + self._token,
                   "Content-Type": "application/json", "Accept": "text/event-stream"}
        if self._account is not None:
            headers["ChatGPT-Account-ID"] = self._account
        operation = _Operation(started, deadline)
        connection = None
        upstream = None
        try:
            if register_cancel is not None:
                try:
                    register_cancel(operation.expire)
                except Exception:
                    raise GatewayError(500, "invalid_cancel_registration") from None
            operation.remaining()
            port = self._port or (443 if self._scheme == "https" else 80)
            addresses = _resolve(self._host, port, operation)
            connection = _FixedConnection(self._host, port, self._scheme, operation, addresses)
            connection.request("POST", self._prefix + "/responses", body, headers)
            transport_socket = connection.sock
            upstream = connection.getresponse()
            operation.remaining()
            if 300 <= upstream.status < 400:
                raise GatewayError(502, "upstream_redirect")
            response_headers = _response_headers(upstream)
            if upstream.status >= 400:
                status = upstream.status
                upstream.close()
                connection.close()
                operation.close()
                body = b'{"error":{"type":"gateway_error","code":"upstream_status"}}'
                return GatewayResponse(status, "application/json", body=body,
                                       headers=response_headers)
            if upstream.status != 200 or upstream.getheader("Content-Encoding", "identity") != "identity":
                raise GatewayError(502, "upstream_content_type")
            if upstream.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "text/event-stream":
                raise GatewayError(502, "upstream_content_type")
            expected_length = upstream.getheader("Content-Length")
            if expected_length is not None:
                expected_length = expected_length.strip(" \t")
                if (upstream.chunked or len(expected_length) > 10
                        or re.fullmatch(r"[0-9]+", expected_length) is None):
                    raise GatewayError(502, "upstream_invalid_headers")
                expected_length = int(expected_length)
                if expected_length > MAX_RESPONSE_BYTES:
                    raise GatewayError(502, "upstream_limit")
            operation.body_phase()
            return GatewayResponse(200, "text/event-stream", upstream=upstream,
                                   connection=connection, transport_socket=transport_socket,
                                   operation=operation,
                                   names=_tools(validated.get("tools", [])),
                                   model=self._model, headers=response_headers,
                                   history=validated["input"] if isinstance(validated["input"], list) else (),
                                   expected_length=expected_length)
        except GatewayError:
            operation.close()
            if upstream is not None:
                upstream.close()
            if connection is not None:
                connection.close()
            raise
        except (socket.timeout, TimeoutError):
            operation.close()
            if upstream is not None:
                upstream.close()
            if connection is not None:
                connection.close()
            raise GatewayError(504, "upstream_timeout") from None
        except (OSError, http.client.HTTPException):
            expired = operation.expired
            operation.close()
            if upstream is not None:
                upstream.close()
            if connection is not None:
                connection.close()
            raise GatewayError(504 if expired else 502,
                               "upstream_timeout" if expired else "upstream_io") from None
