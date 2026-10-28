"""Bounded Messages adapter. Authentication and OS confinement belong to its caller.

Protocol references: https://platform.claude.com/docs/en/build-with-claude/streaming
and https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls.
No tool is executed here. HTTP origins are restricted to literal loopback for tests.
"""
import copy
import http.client
import json
import math
import re
import socket
import threading
import time
from urllib.parse import urlsplit

MAX_REQUEST = 2 * 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
MAX_EVENT = 256 * 1024
MAX_EVENTS = 20000
MAX_DEPTH = 32
MAX_NODES = 100000
MAX_HEADERS = 32 * 1024
MAX_DNS_WORKERS = 4
_DNS_SLOTS = threading.BoundedSemaphore(MAX_DNS_WORKERS)
_SSE_EVENT_END = re.compile(br'\r?\n\r?\n')
DEADLINE_SECONDS = 90
CLIENT_TOOLS = frozenset(('Bash', 'Read', 'Edit', 'Write', 'Glob', 'Grep',
                          'NotebookEdit', 'TodoWrite', 'TaskCreate', 'TaskGet',
                          'TaskUpdate', 'TaskList'))


class GatewayError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        self.headers = {}
        super().__init__(code)


def _require(ok, code='invalid_request', status=400):
    if not ok:
        raise GatewayError(status, code)


def _keys(value, allowed, required=()):
    _require(isinstance(value, dict) and set(value) <= set(allowed)
             and set(required) <= set(value))


def _text(value, limit=262144):
    _require(isinstance(value, str))
    try:
        _require(len(value.encode('utf-8')) <= limit)
    except UnicodeError:
        raise GatewayError(400, 'invalid_text')


def _integer(value, low=0, high=1000000):
    _require(type(value) is int and low <= value <= high)


def _bounded(value, depth=0, budget=None):
    if budget is None:
        budget = [MAX_NODES]
    budget[0] -= 1
    _require(depth <= MAX_DEPTH and budget[0] >= 0, 'structure_limit')
    if isinstance(value, dict):
        _require(len(value) <= 1024)
        for k, v in value.items():
            _text(k, 1024)
            _bounded(v, depth + 1, budget)
    elif isinstance(value, list):
        _require(len(value) <= 8192)
        for v in value:
            _bounded(v, depth + 1, budget)
    elif isinstance(value, str):
        _text(value, MAX_REQUEST)
    else:
        _require(value is None or type(value) in (int, bool, float))
        if type(value) is float:
            _require(math.isfinite(value))


def _pairs(pairs):
    result = {}
    for k, v in pairs:
        _require(k not in result, 'duplicate_key')
        result[k] = v
    return result


def _load(raw):
    try:
        result = json.loads(raw, object_pairs_hook=_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        _bounded(result)
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise GatewayError(400, 'invalid_json')


def _cache(value):
    _keys(value, ('type', 'ttl'), ('type',))
    _require(value['type'] == 'ephemeral' and value.get('ttl', '5m') in ('5m', '1h'))


def _schema(schema):
    positions, references = set(), []
    def visit(s, path, depth):
        _require(depth <= MAX_DEPTH and len(positions) < 4096, 'schema_limit')
        positions.add(path)
        if isinstance(s, bool):
            return
        allowed = ('$schema', '$ref', '$defs', 'definitions', 'type', 'properties',
                   'required', 'additionalProperties', 'items', 'prefixItems', 'enum',
                   'const', 'anyOf', 'oneOf', 'allOf', 'not', 'description', 'title',
                   'default', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum',
                   'multipleOf', 'minLength', 'maxLength', 'pattern', 'format', 'minItems',
                   'maxItems', 'uniqueItems', 'minProperties', 'maxProperties')
        _keys(s, allowed)
        if '$schema' in s:
            _require(s['$schema'] in ('https://json-schema.org/draft/2020-12/schema',
                                      'http://json-schema.org/draft-07/schema#'))
        if '$ref' in s:
            ref = s['$ref']
            _require(isinstance(ref, str) and (ref == '#' or ref.startswith('#/')), 'remote_schema')
            _require(not re.search(r'~(?:[^01]|$)', ref), 'invalid_schema_ref')
            target = () if ref == '#' else tuple(part.replace('~1', '/').replace('~0', '~')
                                                 for part in ref[2:].split('/'))
            references.append(target)
        if 'type' in s:
            types = s['type'] if isinstance(s['type'], list) else [s['type']]
            _require(bool(types) and all(t in ('object', 'array', 'string', 'number',
                                              'integer', 'boolean', 'null') for t in types))
        for k in ('properties', '$defs', 'definitions'):
            if k in s:
                _require(isinstance(s[k], dict))
                for name, v in s[k].items():
                    visit(v, path + (k, name), depth + 1)
        for k in ('items', 'additionalProperties', 'not'):
            if k in s:
                visit(s[k], path + (k,), depth + 1)
        for k in ('anyOf', 'oneOf', 'allOf', 'prefixItems'):
            if k in s:
                _require(isinstance(s[k], list) and 0 < len(s[k]) <= 128)
                for index, v in enumerate(s[k]):
                    visit(v, path + (k, str(index)), depth + 1)
        if 'required' in s:
            _require(isinstance(s['required'], list) and all(isinstance(x, str) for x in s['required']))
            _require(len(s['required']) == len(set(s['required'])))
        for k in ('description', 'title', 'pattern', 'format'):
            if k in s: _text(s[k], 65536)
        for k in ('minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf',
                  'minLength', 'maxLength', 'minItems', 'maxItems', 'minProperties', 'maxProperties'):
            if k in s: _require(type(s[k]) in (int, float))
        if 'uniqueItems' in s: _require(type(s['uniqueItems']) is bool)
        if 'enum' in s: _require(isinstance(s['enum'], list) and 0 < len(s['enum']) <= 1024)
    visit(schema, (), 0)
    # Annotation values are data, never schema targets. This finite graph also
    # permits recursive local schemas without recursively expanding references.
    _require(len(references) <= 4096 and all(ref in positions for ref in references),
             'invalid_schema_ref')


def _block(b, role, names):
    _require(isinstance(b, dict))
    t = b.get('type')
    if t == 'text':
        _keys(b, ('type', 'text', 'cache_control'), ('type', 'text'))
        _text(b['text'])
    elif t == 'thinking' and role == 'assistant':
        _keys(b, ('type', 'thinking', 'signature'), ('type', 'thinking', 'signature'))
        _text(b['thinking']); _text(b['signature'])
    elif t == 'redacted_thinking' and role == 'assistant':
        _keys(b, ('type', 'data'), ('type', 'data')); _text(b['data'])
    elif t == 'tool_use' and role == 'assistant':
        _keys(b, ('type', 'id', 'name', 'input', 'cache_control'), ('type', 'id', 'name', 'input'))
        _text(b['id'], 128)
        _require(isinstance(b['name'], str) and b['name'] in names and isinstance(b['input'], dict), 'undeclared_tool')
    elif t == 'tool_result' and role == 'user':
        _keys(b, ('type', 'tool_use_id', 'content', 'is_error', 'cache_control'), ('type', 'tool_use_id'))
        _text(b['tool_use_id'], 128)
        _require(type(b.get('is_error', False)) is bool)
        content = b.get('content', '')
        if isinstance(content, str):
            _text(content)
        else:
            _require(isinstance(content, list) and len(content) <= 256)
            for v in content:
                _require(isinstance(v, dict) and v.get('type') == 'text')
                _block(v, 'user', names)
    else:
        raise GatewayError(400, 'unsupported_content')
    if 'cache_control' in b:
        _cache(b['cache_control'])


def validate_request(doc, fixed_model):
    """Parse bytes/JSON or copy a dict; accept only an explicitly pinned model map."""
    if isinstance(doc, (str, bytes, bytearray)):
        _require(len(doc) <= MAX_REQUEST, 'request_too_large', 413)
        doc = _load(doc)
    else:
        _bounded(doc)
    _keys(doc, ('model', 'messages', 'system', 'tools', 'tool_choice', 'metadata',
                'max_tokens', 'thinking', 'output_config', 'stream', 'temperature',
                'top_p', 'top_k', 'stop_sequences', 'context_management'),
          ('model', 'messages', 'max_tokens'))
    mapping = {fixed_model: fixed_model} if isinstance(fixed_model, str) else fixed_model
    _require(isinstance(mapping, dict) and isinstance(doc['model'], str) and doc['model'] in mapping, 'model_not_allowed')
    result = copy.deepcopy(doc)
    result['model'] = mapping[doc['model']]
    _text(result['model'], 128)
    _integer(doc['max_tokens'], 1, 128000)
    _require(doc.get('stream', False) is True, 'stream_required')
    tools = doc.get('tools', [])
    _require(isinstance(tools, list) and len(tools) <= 64)
    names = set()
    for tool in tools:
        _keys(tool, ('name', 'description', 'input_schema', 'cache_control', 'strict'),
              ('name', 'input_schema'))
        name = tool['name']
        _require(isinstance(name, str) and name in CLIENT_TOOLS and name not in names,
                 'unsupported_tool')
        names.add(name)
        _schema(tool['input_schema'])
        _require(isinstance(tool['input_schema'], dict) and tool['input_schema'].get('type') == 'object')
        if 'description' in tool: _text(tool['description'], 65536)
        if 'cache_control' in tool: _cache(tool['cache_control'])
        if 'strict' in tool: _require(type(tool['strict']) is bool)
    if 'tool_choice' in doc:
        c = doc['tool_choice']
        _keys(c, ('type', 'name', 'disable_parallel_tool_use'), ('type',))
        _require(c['type'] in ('auto', 'any', 'none', 'tool'))
        _require((isinstance(c.get('name'), str) and c['name'] in names) if c['type'] == 'tool' else 'name' not in c)
        _require(type(c.get('disable_parallel_tool_use', False)) is bool)
    if 'system' in doc:
        system = doc['system']
        if isinstance(system, str): _text(system)
        else:
            _require(isinstance(system, list) and len(system) <= 128)
            for b in system:
                _require(isinstance(b, dict) and b.get('type') == 'text')
                _block(b, 'user', names)
    messages = doc['messages']
    _require(isinstance(messages, list) and 0 < len(messages) <= 4096)
    pending, used = [], set()
    for message in messages:
        _keys(message, ('role', 'content'), ('role', 'content'))
        role, content = message['role'], message['content']
        _require(role in ('user', 'assistant'))
        if isinstance(content, str):
            _text(content); content = []
        else: _require(isinstance(content, list) and len(content) <= 1024)
        results, calls = [], []
        for b in content:
            _block(b, role, names)
            if b['type'] == 'tool_use':
                _require(b['id'] not in used, 'duplicate_tool_id')
                used.add(b['id']); calls.append(b['id'])
            if b['type'] == 'tool_result': results.append(b['tool_use_id'])
        if results:
            _require(all(b['type'] == 'tool_result' for b in content[:len(results)]), 'tool_continuity')
        if pending:
            _require(role == 'user' and results == pending, 'tool_continuity')
        else: _require(not results, 'tool_continuity')
        pending = calls
    _require(not pending, 'tool_continuity')
    if 'metadata' in doc:
        _keys(doc['metadata'], ('user_id',))
        if 'user_id' in doc['metadata']: _text(doc['metadata']['user_id'], 1024)
    if 'thinking' in doc:
        t = doc['thinking']; _keys(t, ('type', 'budget_tokens'), ('type',))
        _require(t['type'] in ('adaptive', 'enabled', 'disabled'))
        if t['type'] == 'enabled': _integer(t.get('budget_tokens'), 1024, doc['max_tokens'] - 1)
        else: _require('budget_tokens' not in t)
    if 'output_config' in doc:
        _keys(doc['output_config'], ('effort',))
        _require(doc['output_config'].get('effort') in ('low', 'medium', 'high', 'max', 'xhigh'))
    if 'context_management' in doc:
        c = doc['context_management']; _keys(c, ('edits',), ('edits',))
        _require(isinstance(c['edits'], list) and len(c['edits']) <= 1)
        for edit in c['edits']:
            _keys(edit, ('type', 'keep'), ('type',))
            _require(edit['type'] == 'clear_thinking_20251015')
            if 'keep' in edit:
                keep = edit['keep']
                if keep != 'all':
                    _keys(keep, ('type', 'value'), ('type', 'value'))
                    _require(keep['type'] == 'thinking_turns'); _integer(keep['value'], 1, 100)
    for k in ('temperature', 'top_p'):
        if k in doc: _require(type(doc[k]) in (int, float) and 0 <= doc[k] <= 1)
    if 'top_k' in doc: _integer(doc['top_k'], 0, 500)
    if 'stop_sequences' in doc:
        _require(isinstance(doc['stop_sequences'], list) and len(doc['stop_sequences']) <= 32)
        for v in doc['stop_sequences']: _text(v, 1024)
    _require(len(json.dumps(result).encode()) <= MAX_REQUEST, 'request_too_large', 413)
    return result


class _Events:
    def __init__(self, model, names):
        self.model, self.names = model, names
        self.started = self.ended = self.delta = self.failed = False
        self.block = None
        self.index = 0
        self.ids = set()
        self.tool_json = ''
        self.signature_started = False

    def event(self, raw):
        try:
            raw = raw.decode('utf-8')
        except UnicodeError:
            raise GatewayError(502, 'invalid_stream')
        lines = raw.splitlines()
        _require(len(lines) == 2 and lines[0].startswith('event: ') and lines[1].startswith('data: '), 'invalid_stream', 502)
        name = lines[0][7:]; d = _load(lines[1][6:])
        _require(isinstance(d, dict) and d.get('type') == name and not self.ended, 'invalid_stream', 502)
        _require(not self.delta or name in ('ping', 'message_stop'), 'event_after_finish', 502)
        if name == 'ping':
            _keys(d, ('type',)); return
        if name == 'error':
            _keys(d, ('type', 'error'), ('type', 'error'))
            _keys(d['error'], ('type', 'message'), ('type', 'message'))
            _text(d['error']['type'], 128); _text(d['error']['message'])
            self.failed = True; self.ended = True; return
        if name == 'message_start':
            _require(not self.started, 'invalid_stream', 502)
            _keys(d, ('type', 'message'), ('type', 'message'))
            m = d['message']
            _keys(m, ('id', 'type', 'role', 'model', 'content', 'stop_reason', 'stop_sequence', 'usage'),
                  ('id', 'type', 'role', 'model', 'content', 'usage'))
            _require(m['type'] == 'message' and m['role'] == 'assistant' and m['model'] == self.model
                     and m['content'] == [] and m.get('stop_reason') is None, 'unexpected_model_or_message', 502)
            _text(m['id'], 128); self._usage(m['usage']); self.started = True; return
        _require(self.started, 'invalid_stream', 502)
        if name in ('content_block_start', 'content_block_delta', 'content_block_stop'):
            _integer(d.get('index'), 0, 4096)
        if name == 'content_block_start':
            _keys(d, ('type', 'index', 'content_block'), ('type', 'index', 'content_block'))
            _require(self.block is None and not self.delta and d['index'] == self.index, 'invalid_stream', 502)
            b = d['content_block']; t = b.get('type') if isinstance(b, dict) else None
            if t == 'text': _keys(b, ('type', 'text'), ('type', 'text')); _text(b['text'])
            elif t == 'thinking':
                _keys(b, ('type', 'thinking', 'signature'), ('type', 'thinking')); _text(b['thinking'])
                if 'signature' in b: _text(b['signature'])
            elif t == 'redacted_thinking': _keys(b, ('type', 'data'), ('type', 'data')); _text(b['data'])
            elif t == 'tool_use':
                _keys(b, ('type', 'id', 'name', 'input'), ('type', 'id', 'name', 'input'))
                _text(b['id'], 128)
                _require(isinstance(b['name'], str) and b['name'] in self.names and b['id'] not in self.ids and b['input'] == {}, 'unexpected_tool', 502)
                self.ids.add(b['id']); self.tool_json = ''
            else: raise GatewayError(502, 'unsupported_response_block')
            self.block = t
            self.signature_started = bool(b.get('signature')) if t == 'thinking' else False
        elif name == 'content_block_delta':
            _keys(d, ('type', 'index', 'delta'), ('type', 'index', 'delta'))
            _require(self.block is not None and d['index'] == self.index, 'invalid_stream', 502)
            v = d['delta']; t = v.get('type') if isinstance(v, dict) else None
            _require(isinstance(t, str), 'invalid_stream', 502)
            field = {'text_delta': ('text', 'text'), 'thinking_delta': ('thinking', 'thinking'),
                     'signature_delta': ('signature', 'thinking'), 'input_json_delta': ('partial_json', 'tool_use')}.get(t)
            _require(field is not None and self.block == field[1], 'invalid_stream', 502)
            _keys(v, ('type', field[0]), ('type', field[0])); _text(v[field[0]])
            if t == 'thinking_delta':
                _require(not self.signature_started, 'thinking_after_signature', 502)
            if t == 'signature_delta':
                self.signature_started = True
            if t == 'input_json_delta':
                self.tool_json += v['partial_json']; _text(self.tool_json)
        elif name == 'content_block_stop':
            _keys(d, ('type', 'index'), ('type', 'index'))
            _require(self.block is not None and d['index'] == self.index, 'invalid_stream', 502)
            if self.block == 'tool_use': _require(isinstance(_load(self.tool_json or '{}'), dict), 'invalid_tool_json', 502)
            if self.block == 'thinking': _require(self.signature_started, 'missing_signature', 502)

            self.block = None; self.index += 1
        elif name == 'message_delta':
            _keys(d, ('type', 'delta', 'usage', 'context_management'), ('type', 'delta', 'usage'))
            _require(self.block is None, 'invalid_stream', 502)
            _keys(d['delta'], ('stop_reason', 'stop_sequence'), ('stop_reason',))
            _require(d['delta']['stop_reason'] in ('end_turn', 'max_tokens', 'stop_sequence', 'tool_use', 'pause_turn', 'refusal'), 'invalid_stream', 502)
            _require((d['delta']['stop_reason'] == 'tool_use') == bool(self.ids), 'invalid_stream', 502)
            if d['delta'].get('stop_sequence') is not None: _text(d['delta']['stop_sequence'], 1024)
            self._usage(d['usage'])
            if 'context_management' in d:
                context = d['context_management']
                _keys(context, ('applied_edits',), ('applied_edits',))
                _require(isinstance(context['applied_edits'], list) and len(context['applied_edits']) <= 1)
                for edit in context['applied_edits']:
                    _keys(edit, ('type', 'cleared_thinking_turns', 'cleared_input_tokens'),
                          ('type', 'cleared_thinking_turns', 'cleared_input_tokens'))
                    _require(edit['type'] == 'clear_thinking_20251015')
                    _integer(edit['cleared_thinking_turns'], 0, 1000000)
                    _integer(edit['cleared_input_tokens'], 0, 1000000000)
            self.delta = True
        elif name == 'message_stop':
            _keys(d, ('type',)); _require(self.delta and self.block is None, 'invalid_stream', 502); self.ended = True
        else: raise GatewayError(502, 'unsupported_event')

    @staticmethod
    def _usage(u):
        _keys(u, ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens',
                  'cache_creation', 'service_tier', 'inference_geo'))
        for k, v in u.items():
            if k in ('service_tier', 'inference_geo'): _text(v, 128)
            elif k == 'cache_creation':
                _keys(v, ('ephemeral_5m_input_tokens', 'ephemeral_1h_input_tokens'))
                for n in v.values(): _integer(n, 0, 1000000000)
            else: _integer(v, 0, 1000000000)


def _retry_headers(response):
    return {k.lower(): v for k, v in response.getheaders()
            if k.lower() in ('retry-after', 'x-should-retry')
            or k.lower().startswith('anthropic-ratelimit-unified-')}


class _FramedHTTPResponse(http.client.HTTPResponse):
    def begin(self):
        super().begin()
        lengths = self.headers.get_all('Content-Length', [])
        transfers = self.headers.get_all('Transfer-Encoding', [])
        _require(len(lengths) <= 1 and len(transfers) <= 1 and not (lengths and transfers),
                 'invalid_http_framing', 502)
        if lengths:
            _require(re.fullmatch(r'[0-9]+', lengths[0].strip()) is not None
                     and self.length is not None, 'invalid_http_framing', 502)
        if transfers:
            _require(transfers[0].lower() == 'chunked' and self.chunked,
                     'invalid_http_framing', 502)

    def _safe_read(self, amount):
        data = super()._safe_read(amount)
        if self.chunked and self.chunk_left == 0 and amount == 2:
            _require(data == b'\r\n', 'invalid_chunk_footer', 502)
        return data

    def _read_next_chunk_size(self):
        line = self.fp.readline(MAX_HEADERS + 1)
        _require(len(line) <= MAX_HEADERS and line.endswith(b'\r\n'), 'invalid_chunk_size', 502)
        size = line[:-2].split(b';', 1)[0]
        _require(re.fullmatch(br'[0-9A-Fa-f]{1,16}', size) is not None, 'invalid_chunk_size', 502)
        return int(size, 16)

    def _read_and_discard_trailer(self):
        # The stdlib tolerates trailer EOF; successful inference requires its blank line.
        total = 0
        for _ in range(100):
            line = self.fp.readline(MAX_HEADERS + 1)
            total += len(line)
            _require(total <= MAX_HEADERS and line.endswith(b'\r\n'), 'invalid_chunk_trailer', 502)
            if line == b'\r\n': return
            _require(re.fullmatch(br"[!#$%&'*+.^_`|~0-9A-Za-z-]+:[^\r\n]*\r\n", line) is not None,
                     'invalid_chunk_trailer', 502)
        raise GatewayError(502, 'invalid_chunk_trailer')


class _Response:
    def __init__(self, connection, response, timer, expired, model, names, peer):
        self._connection, self._response, self._timer = connection, response, timer
        self._expired = expired
        self._peer = peer
        self._read_lock = threading.Lock()
        self._closed = threading.Event()
        self.status = response.status
        self.content_type = response.getheader('Content-Type', '').split(';')[0].strip().lower()
        self.headers = _retry_headers(response)
        self._events = _Events(model, names)
        self._used = False

    def __enter__(self): return self

    def __exit__(self, *args): self.close()

    def close(self):
        self._closed.set()
        # Wake a concurrent buffered read before closing its file object.
        if self._peer:
            try: self._peer.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        self._timer.cancel()
        with self._read_lock:
            self._response.close()
            self._connection.close()

    def iter_bytes(self):
        """Yield only complete, validated events; EOF never implies success."""
        _require(not self._used, 'response_already_consumed', 502)
        self._used = True
        total, count, buf = 0, 0, b''
        terminal = None
        try:
            _require(self.status == 200 and self.content_type == 'text/event-stream', 'unexpected_response', 502)
            while True:
                if self._expired.is_set(): raise GatewayError(504, 'upstream_deadline')
                with self._read_lock:
                    if self._closed.is_set(): raise GatewayError(499, 'consumer_closed')
                    chunk = self._response.read1(4096)
                    if not chunk and not self._closed.is_set() and not self._expired.is_set():
                        complete = (self._response.chunk_left is None and self._response.isclosed()) if self._response.chunked else self._response.length in (None, 0)
                        _require(complete, 'truncated_http_body', 502)
                if self._closed.is_set(): raise GatewayError(499, 'consumer_closed')
                if not chunk: break
                total += len(chunk)
                _require(total <= MAX_RESPONSE, 'response_too_large', 502)
                buf += chunk
                boundary = _SSE_EVENT_END.search(buf)
                while boundary is not None:
                    if self._expired.is_set(): raise GatewayError(504, 'upstream_deadline')
                    if self._closed.is_set(): raise GatewayError(499, 'consumer_closed')
                    raw, separator, buf = buf[:boundary.start()], buf[boundary.start():boundary.end()], buf[boundary.end():]
                    count += 1
                    _require(len(raw) <= MAX_EVENT and count <= MAX_EVENTS, 'stream_limit', 502)
                    try:
                        self._events.event(raw)
                    except GatewayError as error:
                        raise GatewayError(502, 'invalid_stream_' + error.code)
                    if self._events.ended and not self._events.failed:
                        terminal = raw + separator
                    else:
                        yield raw + separator
                    boundary = _SSE_EVENT_END.search(buf)
                _require(len(buf) <= MAX_EVENT, 'stream_limit', 502)
            if self._expired.is_set(): raise GatewayError(504, 'upstream_deadline')
            _require(not buf and self._events.ended, 'truncated_stream', 502)
            if self._closed.is_set(): raise GatewayError(499, 'consumer_closed')
            if terminal is not None: yield terminal
        except GatewayError:
            if self._expired.is_set(): raise GatewayError(504, 'upstream_deadline')
            if self._closed.is_set(): raise GatewayError(499, 'consumer_closed')
            raise
        except (OSError, http.client.HTTPException):
            raise GatewayError(504 if self._expired.is_set() else 502,
                               'upstream_deadline' if self._expired.is_set() else 'upstream_io')
        finally:
            self.close()


class _DeadlineHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, deadline, expired, peer):
        super().__init__(host, port, timeout=max(.001, deadline - time.monotonic()))
        self._deadline, self._expired, self._peer = deadline, expired, peer

    def connect(self):
        http.client.HTTPConnection.connect(self)
        if self._expired.is_set(): raise socket.timeout('TLS deadline')
        # Publish the TLS socket before its handshake so cancellation reaches it.
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host,
                                              do_handshake_on_connect=False)
        self._peer[0] = self.sock
        remaining = self._deadline - time.monotonic()
        if self._expired.is_set() or remaining <= 0:
            raise socket.timeout('TLS deadline')
        self.sock.settimeout(remaining)
        self.sock.do_handshake()


class AnthropicGateway:
    def __init__(self, origin, model, token, oauth_beta=None):
        u = urlsplit(origin)
        _require(u.scheme in ('http', 'https') and u.hostname and not u.username
                 and not u.password and u.path in ('', '/') and not u.query and not u.fragment,
                 'invalid_origin')
        _require(u.scheme == 'https' or u.hostname in ('127.0.0.1', '::1'), 'insecure_origin')
        _text(token, 16384)
        _require(bool(token) and '\r' not in token and '\n' not in token, 'invalid_token')
        if oauth_beta is not None:
            _text(oauth_beta, 1024)
            _require(bool(re.fullmatch(r'[a-z0-9,-]+', oauth_beta)), 'invalid_beta')
        self._origin, self.model, self._token, self._beta = u, model, token, oauth_beta

    def request(self, path, document, *, deadline=None, register_cancel=None):
        adapter_deadline = time.monotonic() + DEADLINE_SECONDS
        if deadline is not None:
            _require(type(deadline) in (int, float) and math.isfinite(deadline), 'invalid_deadline')
            adapter_deadline = min(adapter_deadline, deadline)
        _require(register_cancel is None or callable(register_cancel), 'invalid_cancel_registration')
        _require(path in ('/v1/messages', '/v1/messages?beta=true'), 'unsupported_path', 404)
        doc = validate_request(document, self.model)
        body = json.dumps(doc, separators=(',', ':'), ensure_ascii=False).encode()
        u = self._origin
        expired, wake = threading.Event(), threading.Event()
        peer = [None]
        deadline = adapter_deadline
        if u.scheme == 'https':
            connection = _DeadlineHTTPSConnection(u.hostname, u.port, deadline, expired, peer)
        else:
            connection = http.client.HTTPConnection(u.hostname, u.port, timeout=DEADLINE_SECONDS)
        connection.response_class = _FramedHTTPResponse
        def create_connection(address, timeout=None, source_address=None):
            # A resolver may ignore socket timeouts; it never owns a sending socket.
            _require(not expired.is_set() and time.monotonic() < deadline, 'upstream_deadline', 504)
            _require(_DNS_SLOTS.acquire(blocking=False), 'resolver_capacity', 503)
            resolved, done = [], threading.Event()
            def resolve():
                try:
                    resolved.append(socket.getaddrinfo(address[0], address[1], 0, socket.SOCK_STREAM))
                except OSError as error:
                    resolved.append(error)
                finally:
                    _DNS_SLOTS.release()
                    done.set()
                    wake.set()
            resolver = threading.Thread(target=resolve, daemon=True)
            try:
                resolver.start()
            except BaseException:
                _DNS_SLOTS.release()
                raise
            wake.wait(max(0, deadline - time.monotonic()))
            if expired.is_set() or not done.is_set() or time.monotonic() >= deadline:
                raise socket.timeout('resolution deadline')
            if isinstance(resolved[0], OSError):
                raise resolved[0]
            failure = OSError('no upstream address')
            for family, kind, protocol, _, target in resolved[0]:
                if expired.is_set() or time.monotonic() >= deadline:
                    raise socket.timeout('connection deadline')
                sock = socket.socket(family, kind, protocol)
                peer[0] = sock
                try:
                    sock.settimeout(max(.001, deadline - time.monotonic()))
                    if expired.is_set(): raise socket.timeout('connection cancelled')
                    sock.connect(target)
                    return sock
                except OSError as error:
                    failure = error
                    sock.close()
            raise failure
        connection._create_connection = create_connection
        def expire():
            expired.set()
            wake.set()
            sock = connection.sock or peer[0]
            if sock:
                try: sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
            # HTTPResponse mutates buffered-reader state; only its reader closes it.
            if sock: sock.close()
        timer = threading.Timer(max(0, deadline - time.monotonic()), expire)
        timer.daemon = True
        try:
            if register_cancel is not None: register_cancel(expire)
            _require(not expired.is_set() and time.monotonic() < deadline, 'upstream_deadline', 504)
            timer.interval = max(0, deadline - time.monotonic())
            timer.start()
            headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream',
                       'Authorization': 'Bearer ' + self._token, 'anthropic-version': '2023-06-01'}
            if self._beta: headers['anthropic-beta'] = self._beta
            connection.request('POST', path, body, headers)
            peer[0] = connection.sock
            response = connection.getresponse()
            _require(not expired.is_set(), 'upstream_deadline', 504)
            # Parsed-header cap; stdlib's per-line/count bounds apply before allocation.
            header_bytes = sum(len(k.encode('latin-1')) + len(v.encode('latin-1')) + 4
                               for k, v in response.getheaders())
            if header_bytes > MAX_HEADERS:
                response.close()
                raise GatewayError(502, 'upstream_headers_too_large')
            if response.status != 200:
                status = response.status
                error = GatewayError(status if status in (401, 403, 429, 500, 502, 503, 529) else 502,
                                     'upstream_status_%d' % status)
                error.headers = _retry_headers(response)
                response.close()
                raise error
            return _Response(connection, response, timer, expired, doc['model'],
                             {t['name'] for t in doc.get('tools', [])}, peer[0])
        except (OSError, http.client.HTTPException):
            timed_out = expired.is_set() or time.monotonic() >= deadline
            timer.cancel(); connection.close()
            raise GatewayError(504 if timed_out else 502,
                               'upstream_deadline' if timed_out else 'upstream_io')
        except BaseException:
            timer.cancel(); connection.close(); raise
