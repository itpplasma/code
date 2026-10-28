"""Synthetic Messages/CLI tests; no credentials, model inference or remote network.

CLI file reads permit system runtime paths outside /Users and the native binary;
this deliberately broad runtime grant is NOT a production confinement profile.
"""
import copy
import http.server
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
from ai_zones import anthropic_gateway as ag

MODEL = 'claude-sonnet-4-6'


def request():
    return {'model': MODEL, 'stream': True, 'max_tokens': 1024,
            'messages': [{'role': 'user', 'content': 'Use https://example.invalid as inert text.'}],
            'tools': [{'name': 'Read', 'input_schema': {'type': 'object',
                       'properties': {'file_path': {'type': 'string'}}, 'required': ['file_path'],
                       'additionalProperties': False}}]}


def events(blocks=None, model=MODEL):
    blocks = [{'type': 'text', 'text': 'Synthetic final.'}] if blocks is None else blocks
    start = {'id': 'msg_synthetic', 'type': 'message', 'role': 'assistant', 'model': model,
             'content': [], 'stop_reason': None, 'stop_sequence': None,
             'usage': {'input_tokens': 10, 'output_tokens': 0}}
    result = [('message_start', {'type': 'message_start', 'message': start}),
              ('ping', {'type': 'ping'})]
    for index, block in enumerate(blocks):
        b = dict(block)
        if b['type'] == 'tool_use':
            payload = {'type': 'input_json_delta', 'partial_json': json.dumps(b['input'])}
            b['input'] = {}
        elif b['type'] == 'thinking':
            payload = {'type': 'thinking_delta', 'thinking': b['thinking']}
            b['thinking'] = ''
        else:
            payload = {'type': 'text_delta', 'text': b.get('text', '')}
            b['text'] = ''
        result.extend([('content_block_start', {'type': 'content_block_start', 'index': index, 'content_block': b}),
                       ('content_block_delta', {'type': 'content_block_delta', 'index': index, 'delta': payload})])
        if b['type'] == 'thinking':
            result.append(('content_block_delta', {'type': 'content_block_delta', 'index': index,
                           'delta': {'type': 'signature_delta', 'signature': 'synthetic-signature'}}))
        result.append(('content_block_stop', {'type': 'content_block_stop', 'index': index}))
    result.extend([('message_delta', {'type': 'message_delta', 'delta': {
        'stop_reason': 'tool_use' if any(b['type'] == 'tool_use' for b in blocks) else 'end_turn',
        'stop_sequence': None}, 'usage': {'output_tokens': 10}}),
        ('message_stop', {'type': 'message_stop'})])
    return [b'event: ' + name.encode() + b'\ndata: ' + json.dumps(data).encode() + b'\n\n'
            for name, data in result]


class Server:
    def __init__(self, callback):
        self.requests = []
        outer = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_HEAD(self): self.send_response(404); self.end_headers()
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                outer.requests.append((self.path, dict(self.headers), raw))
                try: callback(self, raw)
                except (BrokenPipeError, ConnectionResetError, OSError): pass
        self.http = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.http.daemon_threads = True
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:%d' % self.http.server_port

    def close(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def send(handler, data, status=200, content_type='text/event-stream'):
    handler.send_response(status)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    handler.wfile.flush()


class ValidationTests(unittest.TestCase):
    def reject(self, doc):
        with self.assertRaises(ag.GatewayError): ag.validate_request(doc, MODEL)

    def test_request_preserves_order_thinking_signature_and_cache(self):
        d = request()
        d['system'] = [{'type': 'text', 'text': 'first'}, {'type': 'text', 'text': 'second',
                       'cache_control': {'type': 'ephemeral', 'ttl': '1h'}}]
        d['messages'] += [{'role': 'assistant', 'content': [
            {'type': 'thinking', 'thinking': 'synthetic', 'signature': 'signed'},
            {'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {'file_path': 'fixture'}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': [
                {'type': 'text', 'text': 'synthetic fixture'}]}]}]
        d['thinking'] = {'type': 'adaptive'}; d['output_config'] = {'effort': 'high'}
        d['context_management'] = {'edits': [{'type': 'clear_thinking_20251015', 'keep': 'all'}]}
        self.assertEqual(ag.validate_request(d, MODEL), d)
        self.assertEqual(ag.validate_request(d, {MODEL: MODEL}), d)

    def test_duplicate_unknown_fields_and_models(self):
        self.reject('{"model":"x","model":"y"}')
        for key in ('mcp_servers', 'container', 'service_tier', 'headers', 'url', 'api_key'):
            d = request(); d[key] = 'forbidden'; self.reject(d)
        d = request(); d['model'] = 'claude-unapproved'; self.reject(d)
        d = request(); d['stream'] = False; self.reject(d)
        d = request(); d['messages'][0]['unknown'] = True; self.reject(d)

    def test_hosted_tools_resources_and_schema_refs(self):
        for tool in ({'type': 'web_search_20250305', 'name': 'web_search'},
                     {'type': 'code_execution_20250522', 'name': 'code_execution'},
                     {'type': 'bash_20250124', 'name': 'Bash'},
                     {'name': 'mcp_tool', 'input_schema': {'type': 'object'}}):
            d = request(); d['tools'].append(tool); self.reject(d)
        for block in ({'type': 'image', 'source': {'type': 'url', 'url': 'https://example.invalid'}},
                      {'type': 'document', 'source': {'type': 'file', 'file_id': 'file_x'}},
                      {'type': 'mcp_tool_use', 'id': 'x', 'name': 'Read', 'input': {}}):
            d = request(); d['messages'][0]['content'] = [block]; self.reject(d)
        d = request(); d['tools'][0]['input_schema']['properties']['x'] = {'$ref': 'https://example.invalid/schema'}; self.reject(d)
        d = request(); d['tool_choice'] = {'type': 'tool', 'name': 'unknown'}; self.reject(d)
        d = request(); d['tools'] *= 2; self.reject(d)

    def test_schema_reference_closure_and_bounded_cycles(self):
        for annotation in ('default', 'const', 'enum'):
            d = request()
            d['tools'][0]['input_schema'] = {'type': 'object',
                'properties': {'x': {'$ref': '#/' + annotation}},
                annotation: {'$ref': 'https://example.invalid/hidden'}}
            self.reject(d)
        d = request()
        d['tools'][0]['input_schema'] = {'type': 'object', '$defs': {
            'node': {'type': 'object', 'properties': {'next': {'$ref': '#/$defs/node'}}}},
            'properties': {'root': {'$ref': '#/$defs/node'}, 'self': {'$ref': '#'}}}
        self.assertEqual(ag.validate_request(d, MODEL), d)
        for ref in ('#/$defs/missing', '#/properties', '#/properties/bad~2escape'):
            bad = copy.deepcopy(d); bad['tools'][0]['input_schema']['properties']['root']['$ref'] = ref
            self.reject(bad)

    def test_tool_continuity(self):
        base = request()
        base['messages'] += [{'role': 'assistant', 'content': [
            {'type': 'tool_use', 'id': 'a', 'name': 'Read', 'input': {}},
            {'type': 'tool_use', 'id': 'b', 'name': 'Read', 'input': {}}]}, {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 'a', 'content': 'A'},
                {'type': 'tool_result', 'tool_use_id': 'b', 'content': 'B'}]}]
        self.assertEqual(ag.validate_request(base, MODEL), base)
        for ids in (['a', 'a'], ['b', 'a'], ['a'], ['x', 'b']):
            d = copy.deepcopy(base); d['messages'][-1]['content'] = [
                {'type': 'tool_result', 'tool_use_id': x, 'content': 'x'} for x in ids]; self.reject(d)
        d = copy.deepcopy(base); d['messages'][-2]['content'][1]['id'] = 'a'; self.reject(d)
        d = copy.deepcopy(base); d['messages'] = d['messages'][:-1]; self.reject(d)

    def test_limits_and_nonfinite(self):
        d = request(); d['max_tokens'] = True; self.reject(d)
        d = request(); d['temperature'] = float('nan'); self.reject(d)
        d = request(); d['tools'][0]['input_schema']['properties']['x'] = {'unexpected': {}}; self.reject(d)
        self.reject(b' ' * (ag.MAX_REQUEST + 1))


class TransportTests(unittest.TestCase):
    def consume(self, data, doc=None):
        with Server(lambda h, raw: send(h, data)) as s:
            with ag.AnthropicGateway(s.origin, MODEL, 'root-fake').request('/v1/messages', doc or request()) as r:
                return b''.join(r.iter_bytes())

    def test_roundtrip_and_rebuilt_headers_ignores_proxy_env(self):
        frames = events([{'type': 'thinking', 'thinking': 'synthetic'}, {'type': 'text', 'text': 'done'}])
        delta = json.loads(frames[-2].split(b'data: ', 1)[1])
        delta['context_management'] = {'applied_edits': [{'type': 'clear_thinking_20251015',
            'cleared_thinking_turns': 1, 'cleared_input_tokens': 10}]}
        frames[-2] = b'event: message_delta\ndata: ' + json.dumps(delta).encode() + b'\n\n'
        data = b''.join(frames)
        with Server(lambda h, raw: send(h, data)) as s, mock.patch.dict(os.environ, {'HTTP_PROXY': 'http://invalid.invalid:1'}):
            with ag.AnthropicGateway(s.origin, MODEL, 'fake-root-token', 'oauth-2025-04-20').request('/v1/messages?beta=true', request()) as r:
                self.assertEqual(r.status, 200); self.assertEqual(b''.join(r.iter_bytes()), data)
            path, headers, raw = s.requests[0]
            self.assertEqual(path, '/v1/messages?beta=true')
            headers = {k.lower(): v for k, v in headers.items()}
            self.assertEqual(headers['authorization'], 'Bearer fake-root-token')
            self.assertEqual(headers['anthropic-version'], '2023-06-01')
            self.assertEqual(headers['anthropic-beta'], 'oauth-2025-04-20')
            self.assertNotIn('cookie', headers)

    def test_fragmented_lf_crlf_and_utf8_events(self):
        frames = []
        for original in events([{'type': 'text', 'text': 'caf\u00e9 \U0001f408'}]):
            name, payload = original.decode().strip().split('\ndata: ', 1)
            frames.append((name + '\ndata: ' + json.dumps(json.loads(payload), ensure_ascii=False) + '\n\n').encode())
        for newline in (b'\n', b'\r\n'):
            data = b''.join(frames).replace(b'\n', newline)
            def fragmented(h, raw):
                h.send_response(200); h.send_header('Content-Type', 'text/event-stream')
                h.send_header('Content-Length', str(len(data))); h.end_headers()
                for byte in data:
                    h.wfile.write(bytes([byte])); h.wfile.flush()
            with self.subTest(newline=newline), Server(fragmented) as server:
                with ag.AnthropicGateway(server.origin, MODEL, 'fake').request('/v1/messages', request()) as response:
                    read_one = response._response.read1
                    with mock.patch.object(response._response, 'read1', side_effect=lambda size: read_one(1)):
                        self.assertEqual(b''.join(response.iter_bytes()), data)
        # CRLF framing does not bypass event bounds or finish validation.
        malformed = events(); malformed.insert(-1, malformed[-2])
        with self.assertRaises(ag.GatewayError): self.consume(b''.join(malformed).replace(b'\n', b'\r\n'))
        with mock.patch.object(ag, 'MAX_EVENT', 30), self.assertRaises(ag.GatewayError):
            self.consume(b''.join(frames).replace(b'\n', b'\r\n'))

    def test_aggregate_parsed_header_budget(self):
        def oversized(h, raw):
            h.send_response(200); h.send_header('Content-Type', 'text/event-stream')
            h.send_header('X-Padding-A', 'a' * 17000); h.send_header('X-Padding-B', 'b' * 17000)
            h.end_headers(); h.wfile.write(b''.join(events()))
        with Server(oversized) as server, self.assertRaises(ag.GatewayError) as cm:
            ag.AnthropicGateway(server.origin, MODEL, 'fake').request('/v1/messages', request())
        self.assertEqual(cm.exception.code, 'upstream_headers_too_large')

    def test_no_upstream_for_rejected_requests_and_routes(self):
        with Server(lambda h, raw: send(h, b'')) as s:
            gateway = ag.AnthropicGateway(s.origin, MODEL, 'fake')
            for path in ('http://evil/v1/messages', '//evil/v1/messages', '/v1/messages?x=y',
                         '/v1/messages/count_tokens', '/v1/models', '/oauth/token', '/v1/%6dessages'):
                with self.assertRaises(ag.GatewayError): gateway.request(path, request())
            d = request(); d['url'] = s.origin
            with self.assertRaises(ag.GatewayError): gateway.request('/v1/messages', d)
            self.assertEqual(s.requests, [])
        for origin in ('http://example.invalid', 'https://user:pass@example.invalid',
                       'https://example.invalid/path', 'https://example.invalid?x=y'):
            with self.assertRaises(ag.GatewayError): ag.AnthropicGateway(origin, MODEL, 'fake')

    def test_status_and_redirect_do_not_follow(self):
        for status in (301, 302, 307, 308, 401, 429):
            def response(h, raw):
                h.send_response(status); h.send_header('Location', 'http://example.invalid/'); h.send_header('Retry-After', '2'); h.end_headers()
            with self.subTest(status=status), Server(response) as s:
                with self.assertRaises(ag.GatewayError) as cm:
                    ag.AnthropicGateway(s.origin, MODEL, 'fake').request('/v1/messages', request())
                self.assertEqual(cm.exception.status, status if status in (401, 429) else 502)
                self.assertEqual(len(s.requests), 1)
                self.assertEqual(cm.exception.headers, {'retry-after': '2'})

    def test_stream_rejects_model_hosted_tools_undeclared_and_truncation(self):
        bad = [b''.join(events(model='wrong')), b''.join(events())[:-30],
               b''.join(events([{'type': 'tool_use', 'id': 't', 'name': 'Bash', 'input': {}}]))]
        hosted = events(); hosted[2] = hosted[2].replace(b'"type": "text"', b'"type": "server_tool_use"')
        bad.append(b''.join(hosted))
        bad.append(b'event: ping\ndata: {"type":"ping","type":"ping"}\n\n')
        for data in bad:
            with self.subTest(data=data[:60]), self.assertRaises(ag.GatewayError): self.consume(data)
        data = b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"synthetic"}}\n\n'
        self.assertEqual(self.consume(data), data)

    def test_stream_state_transitions_and_decoded_tool_arguments(self):
        def frame(name, data):
            return ('event: ' + name + '\ndata: ' + json.dumps(data) + '\n\n').encode()
        base = events()
        duplicate_finish = base[:-1] + [base[-2], base[-1]]
        duplicate_stop = base + [base[-1]]
        delta_before_start = [base[3]] + base
        duplicate_block_stop = base[:5] + [base[4]] + base[5:]
        wrong_index = list(base)
        wrong = json.loads(wrong_index[3].split(b'data: ', 1)[1]); wrong['index'] = 7
        wrong_index[3] = frame('content_block_delta', wrong)
        thinking = events([{'type': 'thinking', 'thinking': 'synthetic'}])
        after_signature = thinking[:5] + [thinking[3]] + thinking[5:]
        no_signature = thinking[:4] + thinking[5:]
        for parts in (duplicate_finish, duplicate_stop, delta_before_start, duplicate_block_stop,
                      wrong_index, after_signature, no_signature):
            with self.subTest(parts=parts), self.assertRaises(ag.GatewayError):
                self.consume(b''.join(parts))
        for args, limits in (('{"x":1,"x":2}', {}), ('{"x":NaN}', {}),
                             ('{"x":' + '[' * 8 + '0' + ']' * 8 + '}', {'MAX_DEPTH': 4}),
                             ('{"x":[' + ','.join('0' for _ in range(100)) + ']}', {'MAX_NODES': 64})):
            parts = events([{'type': 'tool_use', 'id': 'args', 'name': 'Read', 'input': {}}])
            delta = json.loads(parts[3].split(b'data: ', 1)[1]); delta['delta']['partial_json'] = args
            parts[3] = frame('content_block_delta', delta)
            with self.subTest(args=args), mock.patch.multiple(ag, **(limits or {'MAX_NODES': ag.MAX_NODES})), self.assertRaises(ag.GatewayError):
                self.consume(b''.join(parts))

    def test_content_length_requires_complete_body_before_success_event(self):
        payload = b''.join(events())
        def short(h, raw):
            h.send_response(200); h.send_header('Content-Type', 'text/event-stream')
            h.send_header('Content-Length', str(len(payload) + 100)); h.end_headers()
            h.wfile.write(payload); h.wfile.flush(); h.close_connection = True
        forwarded = []
        with Server(short) as server:
            with ag.AnthropicGateway(server.origin, MODEL, 'fake').request('/v1/messages', request()) as response:
                with self.assertRaises(ag.GatewayError) as cm:
                    for frame in response.iter_bytes(): forwarded.append(frame)
                self.assertEqual(cm.exception.status, 502)
        self.assertIn(b'event: content_block_delta', b''.join(forwarded))
        self.assertNotIn(b'event: message_stop', b''.join(forwarded))
        def exact_persistent(h, raw):
            h.protocol_version = 'HTTP/1.1'
            send(h, payload)
        with Server(exact_persistent) as server:
            with ag.AnthropicGateway(server.origin, MODEL, 'fake').request('/v1/messages', request()) as response:
                self.assertEqual(b''.join(response.iter_bytes()), payload)

    def test_chunked_requires_size_footer_zero_chunk_and_complete_trailer(self):
        payload = b''.join(events())
        chunk = ('%x\r\n' % len(payload)).encode() + payload
        cases = [(chunk + b'\r\n0\r\n\r\n', True),
                 (chunk + b'\r\n0\r\nX-Synthetic: value\r\n\r\n', True),
                 (chunk + b'\r\n', False), (chunk + b'\r\n0\r\n', False),
                 (chunk + b'XX0\r\n\r\n', False),
                 (b'+' + chunk + b'\r\n0\r\n\r\n', False)]
        for wire, valid in cases:
            def chunked(h, raw):
                h.send_response(200); h.send_header('Content-Type', 'text/event-stream')
                h.send_header('Transfer-Encoding', 'chunked'); h.end_headers()
                h.wfile.write(wire); h.wfile.flush(); h.close_connection = True
            with self.subTest(wire=wire[-30:], valid=valid), Server(chunked) as server:
                with ag.AnthropicGateway(server.origin, MODEL, 'fake').request('/v1/messages', request()) as response:
                    forwarded = []
                    if valid:
                        self.assertEqual(b''.join(response.iter_bytes()), payload)
                    else:
                        with self.assertRaises(ag.GatewayError):
                            for frame in response.iter_bytes(): forwarded.append(frame)
                        self.assertNotIn(b'event: message_stop', b''.join(forwarded))

    def test_success_waits_for_chunk_terminator_and_remains_cancellable(self):
        payload = b''.join(events())
        for cancel in (False, True):
            release, waiting, finished = threading.Event(), threading.Event(), threading.Event()
            def chunked(h, raw):
                h.send_response(200); h.send_header('Content-Type', 'text/event-stream')
                h.send_header('Transfer-Encoding', 'chunked'); h.end_headers()
                h.wfile.write(('%x\r\n' % len(payload)).encode() + payload + b'\r\n0\r\n')
                h.wfile.flush(); release.wait(2)
                h.wfile.write(b'\r\n'); h.wfile.flush(); h.close_connection = True
            with self.subTest(cancel=cancel), Server(chunked) as server:
                callbacks, forwarded, errors = [], [], []
                response = ag.AnthropicGateway(server.origin, MODEL, 'fake').request('/v1/messages', request(), register_cancel=callbacks.append)
                def consume():
                    try:
                        for frame in response.iter_bytes():
                            forwarded.append(frame)
                            if frame.startswith(b'event: message_delta'): waiting.set()
                    except ag.GatewayError as error: errors.append(error)
                    finally: finished.set()
                reader = threading.Thread(target=consume, daemon=True); reader.start()
                try:
                    self.assertTrue(waiting.wait(1)); self.assertFalse(finished.wait(.05))
                    self.assertNotIn(b'event: message_stop', b''.join(forwarded))
                    if cancel: callbacks[0]()
                    else: release.set()
                    self.assertTrue(finished.wait(1)); reader.join(1)
                    if cancel:
                        self.assertEqual([error.status for error in errors], [504])
                        self.assertNotIn(b'event: message_stop', b''.join(forwarded))
                    else:
                        self.assertEqual(errors, [])
                        self.assertEqual(b''.join(forwarded), payload)
                finally:
                    release.set(); response.close(); reader.join(2)

    def test_stream_event_total_and_depth_bounds(self):
        with mock.patch.object(ag, 'MAX_EVENT', 30), self.assertRaises(ag.GatewayError): self.consume(b''.join(events()))
        with mock.patch.object(ag, 'MAX_RESPONSE', 30), self.assertRaises(ag.GatewayError): self.consume(b''.join(events()))
        with mock.patch.object(ag, 'MAX_EVENTS', 2), self.assertRaises(ag.GatewayError): self.consume(b''.join(events()))
        data = b'event: ping\ndata: ' + json.dumps({'type': 'ping', 'x': [[[[[[0]]]]]]}).encode() + b'\n\n'
        with mock.patch.object(ag, 'MAX_DEPTH', 3), self.assertRaises(ag.GatewayError): self.consume(data)

    def test_incremental_event_does_not_wait_for_large_buffer(self):
        release = threading.Event()
        first = events()[0]
        def response(h, raw):
            h.send_response(200); h.send_header('Content-Type', 'text/event-stream'); h.end_headers()
            h.wfile.write(first); h.wfile.flush(); release.wait(2)
        with Server(response) as s:
            with ag.AnthropicGateway(s.origin, MODEL, 'fake').request('/v1/messages', request()) as r:
                begin = time.monotonic(); it = r.iter_bytes(); self.assertEqual(next(it), first)
                self.assertLess(time.monotonic() - begin, 1)
                it.close(); release.set()

    def test_absolute_headers_and_dribble_body_deadlines(self):
        def slow_headers(h, raw):
            for byte in b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n':
                h.connection.send(bytes([byte])); time.sleep(.04)
        def slow_body(h, raw):
            h.send_response(200); h.send_header('Content-Type', 'text/event-stream'); h.end_headers()
            for byte in b''.join(events()):
                h.wfile.write(bytes([byte])); h.wfile.flush(); time.sleep(.04)
        def slow_empty_body(h, raw):
            time.sleep(.12)
            h.send_response(200); h.send_header('Content-Type', 'text/event-stream'); h.end_headers()
            time.sleep(2)
        for callback in (slow_headers, slow_body, slow_empty_body):
            with self.subTest(callback=callback.__name__), Server(callback) as s, mock.patch.object(ag, 'DEADLINE_SECONDS', .2):
                begin = time.monotonic()
                with self.assertRaises(ag.GatewayError) as cm:
                    with ag.AnthropicGateway(s.origin, MODEL, 'fake').request('/v1/messages', request()) as r:
                        list(r.iter_bytes())
                self.assertEqual(cm.exception.status, 504)
                self.assertLess(time.monotonic() - begin, 1)

    def test_trusted_caller_deadline_and_cancel_before_headers(self):
        for cancel_early in (False, True):
            entered, closed = threading.Event(), threading.Event()
            callbacks, errors = [], []
            def delayed_headers(h, raw):
                entered.set(); h.connection.settimeout(2)
                if h.connection.recv(1) == b'': closed.set()
            with self.subTest(cancel=cancel_early), Server(delayed_headers) as server:
                gateway = ag.AnthropicGateway(server.origin, MODEL, 'fake')
                def run():
                    try:
                        gateway.request('/v1/messages', request(), deadline=time.monotonic() + (.8 if cancel_early else .15),
                                        register_cancel=callbacks.append)
                    except ag.GatewayError as error: errors.append(error)
                worker = threading.Thread(target=run, daemon=True)
                begin = time.monotonic(); worker.start()
                self.assertTrue(entered.wait(1)); self.assertEqual(len(callbacks), 1)
                if cancel_early: callbacks[0]()
                worker.join(.5)
                self.assertFalse(worker.is_alive(), 'request stayed blocked before headers')
                self.assertEqual([error.status for error in errors], [504])
                self.assertTrue(closed.wait(.5), 'provider socket remained open')
                self.assertLess(time.monotonic() - begin, .65)

    def test_pre_cancel_registration_and_cancelled_dns_never_send(self):
        with Server(lambda h, raw: send(h, b''.join(events()))) as server:
            gateway = ag.AnthropicGateway(server.origin, MODEL, 'fake')
            with mock.patch.object(ag.socket, 'getaddrinfo') as resolve:
                with self.assertRaises(ag.GatewayError) as cm:
                    gateway.request('/v1/messages', request(), register_cancel=lambda cancel: cancel())
                self.assertEqual(cm.exception.status, 504); resolve.assert_not_called()
            with self.assertRaises(ag.GatewayError) as cm:
                gateway.request('/v1/messages', request(), deadline=time.monotonic() - 1)
            self.assertEqual(cm.exception.status, 504)
            started, release, finished = threading.Event(), threading.Event(), threading.Event()
            callbacks, errors = [], []
            original_resolve = socket.getaddrinfo
            def slow_resolver(*args):
                started.set(); release.wait(2)
                try: return original_resolve(*args)
                finally: finished.set()
            def run():
                try: gateway.request('/v1/messages', request(), register_cancel=callbacks.append)
                except ag.GatewayError as error: errors.append(error)
            with mock.patch.object(ag.socket, 'getaddrinfo', side_effect=slow_resolver):
                worker = threading.Thread(target=run, daemon=True); worker.start()
                try:
                    self.assertTrue(started.wait(1)); self.assertEqual(len(callbacks), 1)
                    callbacks[0](); worker.join(.5)
                    self.assertFalse(worker.is_alive(), 'cancelled request waited for DNS')
                    self.assertEqual([error.status for error in errors], [504])
                finally:
                    release.set(); self.assertTrue(finished.wait(1)); worker.join(2)
            self.assertEqual(server.requests, [])

    def test_absolute_deadline_includes_delayed_connect_and_tls_handshake(self):
        context_probe = ag._DeadlineHTTPSConnection('example.invalid', None, time.monotonic() + 1,
                                                   threading.Event(), [None])
        self.assertTrue(context_probe._context.check_hostname)
        self.assertEqual(context_probe._context.verify_mode, ssl.CERT_REQUIRED)
        context_probe.close()
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(1)
        release, accepted = threading.Event(), threading.Event()
        def silent_peer():
            peer, _ = listener.accept()
            try:
                accepted.set(); peer.recv(65536); release.wait(1)
            finally: peer.close()
        server = threading.Thread(target=silent_peer, daemon=True); server.start()
        original_connect = socket.socket.connect
        def delayed_connect(sock, target):
            time.sleep(.15)
            return original_connect(sock, target)
        try:
            with mock.patch.object(ag, 'DEADLINE_SECONDS', .2), mock.patch.object(socket.socket, 'connect', delayed_connect):
                begin = time.monotonic()
                with self.assertRaises(ag.GatewayError) as cm:
                    ag.AnthropicGateway('https://127.0.0.1:%d' % listener.getsockname()[1], MODEL, 'fake').request('/v1/messages', request())
                self.assertEqual(cm.exception.status, 504)
                self.assertTrue(accepted.is_set())
                self.assertLess(time.monotonic() - begin, .30)
        finally:
            release.set(); listener.close(); server.join(2)

    def test_resolver_deadline_does_not_start_connection_later(self):
        released = threading.Event()
        def slow_resolver(*args):
            released.wait(1)
            return []
        with mock.patch.object(ag.socket, 'getaddrinfo', side_effect=slow_resolver), mock.patch.object(ag, 'DEADLINE_SECONDS', .1):
            begin = time.monotonic()
            try:
                with self.assertRaises(ag.GatewayError) as cm:
                    ag.AnthropicGateway('https://example.invalid', MODEL, 'fake').request('/v1/messages', request())
                self.assertEqual(cm.exception.status, 504)
                self.assertLess(time.monotonic() - begin, .5)
            finally:
                released.set()

    def test_stuck_dns_workers_remain_process_bounded(self):
        release, condition = threading.Event(), threading.Condition()
        active, calls, peak = [0], [0], [0]
        def stuck(*args):
            with condition:
                active[0] += 1; calls[0] += 1; peak[0] = max(peak[0], active[0])
            try:
                release.wait(5)
                return []
            finally:
                with condition:
                    active[0] -= 1; condition.notify_all()
        gateway = ag.AnthropicGateway('https://example.invalid', MODEL, 'fake')
        statuses = []
        with mock.patch.object(ag.socket, 'getaddrinfo', side_effect=stuck), mock.patch.object(ag, 'DEADLINE_SECONDS', .04):
            begin = time.monotonic()
            try:
                for _ in range(ag.MAX_DNS_WORKERS + 8):
                    with self.assertRaises(ag.GatewayError) as cm: gateway.request('/v1/messages', request())
                    statuses.append(cm.exception.status)
                self.assertEqual(statuses[:ag.MAX_DNS_WORKERS], [504] * ag.MAX_DNS_WORKERS)
                self.assertEqual(statuses[ag.MAX_DNS_WORKERS:], [503] * 8)
                self.assertEqual(calls[0], ag.MAX_DNS_WORKERS)
                self.assertEqual(peak[0], ag.MAX_DNS_WORKERS)
                self.assertLess(time.monotonic() - begin, 1)
            finally:
                release.set()
                with condition:
                    self.assertTrue(condition.wait_for(lambda: active[0] == 0, timeout=2))
        # Completed abandoned resolvers release their slots for the next request.
        self.assertEqual(self.consume(b''.join(events())), b''.join(events()))

    def test_close_interrupts_concurrent_pending_read(self):
        release, read_started = threading.Event(), threading.Event()
        errors = []
        def hold(h, raw):
            h.send_response(200); h.send_header('Content-Type', 'text/event-stream'); h.end_headers()
            h.wfile.write(events()[0]); h.wfile.flush(); release.wait(2)
        with Server(hold) as server:
            response = ag.AnthropicGateway(server.origin, MODEL, 'fake').request('/v1/messages', request())
            iterator = response.iter_bytes(); next(iterator)
            def read():
                read_started.set()
                try: list(iterator)
                except ag.GatewayError as error: errors.append(error.code)
            reader = threading.Thread(target=read, daemon=True); reader.start()
            self.assertTrue(read_started.wait(1))
            begin = time.monotonic()
            closer = threading.Thread(target=response.close, daemon=True); closer.start()
            try:
                closer.join(.5); reader.join(.5)
                self.assertFalse(closer.is_alive(), 'close blocked behind pending read')
                self.assertFalse(reader.is_alive(), 'pending read was not interrupted')
                self.assertLess(time.monotonic() - begin, 1)
                self.assertTrue(errors)
            finally:
                release.set(); closer.join(2); reader.join(2)

    def test_close_stops_already_buffered_events(self):
        with Server(lambda h, raw: send(h, b''.join(events()))) as server:
            response = ag.AnthropicGateway(server.origin, MODEL, 'fake').request('/v1/messages', request())
            iterator = response.iter_bytes(); next(iterator); response.close()
            with self.assertRaises(ag.GatewayError) as cm: next(iterator)
            self.assertEqual(cm.exception.code, 'consumer_closed')

    def test_consumer_disconnect_closes_upstream(self):
        closed = threading.Event()
        def response(h, raw):
            h.send_response(200); h.send_header('Content-Type', 'text/event-stream'); h.end_headers()
            h.wfile.write(events()[0]); h.wfile.flush()
            h.connection.settimeout(2)
            if h.connection.recv(1) == b'': closed.set()
        with Server(response) as s:
            r = ag.AnthropicGateway(s.origin, MODEL, 'fake').request('/v1/messages', request())
            it = r.iter_bytes(); next(it); it.close()
            self.assertTrue(closed.wait(1))


class InstalledClaudeTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('claude') and Path('/usr/bin/sandbox-exec').exists(),
                         'requires native macOS Claude and Seatbelt')
    def test_installed_cli_read_bash_result_final_and_followup(self):
        cli = str(Path(shutil.which('claude')).resolve())
        with tempfile.TemporaryDirectory(prefix='anthropic-cli-test-') as temp:
            root = Path(temp).resolve(); home = root / 'home'; project = root / 'project'
            home.mkdir(); project.mkdir(); fixture = project / 'fixture.txt'
            fixture.write_text('synthetic fixture value\n')
            seen, errors = [], []
            def upstream(h, raw):
                doc = json.loads(raw); seen.append(doc); n = len(seen)
                if n == 1: blocks = [{'type': 'tool_use', 'id': 'read_fixture', 'name': 'Read', 'input': {'file_path': str(fixture)}}]
                elif n == 2: blocks = [{'type': 'tool_use', 'id': 'bash_fixture', 'name': 'Bash', 'input': {'command': "printf 'synthetic bash value\\n'", 'description': 'Print synthetic value'}}]
                else: blocks = [{'type': 'text', 'text': 'Synthetic followup.' if n == 4 else 'Fixture verified.'}]
                send(h, b''.join(events(blocks)))
            with Server(upstream) as upstream_server:
                gateway = ag.AnthropicGateway(upstream_server.origin, MODEL, 'fake-root-credential')
                def frontend(h, raw):
                    try:
                        self.assertEqual(h.headers.get('Authorization'), 'Bearer dummy-local-capability')
                        with gateway.request(h.path, raw) as response:
                            h.send_response(response.status); h.send_header('Content-Type', response.content_type)
                            h.end_headers()
                            for chunk in response.iter_bytes(): h.wfile.write(chunk); h.wfile.flush()
                    except BaseException as error:
                        errors.append(repr(error)); send(h, b'{"error":"rejected"}', 400, 'application/json')
                with Server(frontend) as front:
                    # System runtime reads are broad; home data, Mach IPC and non-loopback networking are denied.
                    profile = ('(version 1)(deny default)(allow process*)(allow sysctl-read)'
                               '(allow file-read-metadata)(allow file-read* (require-not (subpath "/Users")))'
                               '(allow file-read* (literal %s))(allow file-write* (subpath %s) (literal "/dev/null"))'
                               '(allow network-outbound (remote tcp "localhost:%d"))') % (
                                   json.dumps(cli), json.dumps(str(root)), front.http.server_port)
                    profile_path = root / 'profile.sb'; profile_path.write_text(profile)
                    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'TMPDIR': str(root),
                           'CLAUDE_CONFIG_DIR': str(home / 'config'), 'CLAUDE_CODE_TMPDIR': str(root / 'cli-tmp'), 'SHELL': '/bin/bash',
                           'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
                           'CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS': '1',
                           'ANTHROPIC_AUTH_TOKEN': 'dummy-local-capability', 'ANTHROPIC_BASE_URL': front.origin,
                           'ANTHROPIC_SMALL_FAST_MODEL': MODEL, 'CLAUDE_CODE_SUBAGENT_MODEL': MODEL}
                    version = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile_path), cli, '--version'],
                                             cwd=project, env=env, capture_output=True, text=True, timeout=15)
                    self.assertEqual(version.returncode, 0, version.stderr)
                    self.assertIn('2.1.310', version.stdout)
                    settings = {'permissions': {'allow': ['Read', 'Bash(printf *)'],
                                                'deny': ['WebFetch', 'WebSearch']},
                                'enableAllProjectMcpServers': False}
                    cmd = ['/usr/bin/sandbox-exec', '-f', str(profile_path), cli, '--safe-mode',
                           '--setting-sources', '', '--settings', json.dumps(settings), '--strict-mcp-config',
                           '--mcp-config', '{"mcpServers":{}}', '--no-chrome', '--disable-slash-commands',
                           '--no-session-persistence', '--model', MODEL, '--tools', 'Read,Bash',
                           '--allowedTools', 'Read,Bash(printf *)', '--system-prompt', 'Use synthetic fixtures only.',
                           '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose', '-p']
                    proc = subprocess.Popen(cmd, cwd=project, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, text=True)
                    output = queue.Queue()
                    def read_lines():
                        for line in proc.stdout: output.put(line)
                        output.put(None)
                    reader = threading.Thread(target=read_lines, daemon=True); reader.start()
                    def turn(text):
                        proc.stdin.write(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': text}}) + '\n')
                        proc.stdin.flush()
                        until = time.monotonic() + 25
                        while time.monotonic() < until:
                            line = output.get(timeout=max(.01, until - time.monotonic()))
                            self.assertIsNotNone(line, 'CLI exited without result')
                            value = json.loads(line)
                            if value.get('type') == 'result': return value
                        self.fail('CLI turn timed out')
                    try:
                        first = turn('Read fixture.txt, print a synthetic value with printf, and report.')
                        self.assertEqual(first.get('result'), 'Fixture verified.', first)
                        second = turn('Give a synthetic followup.')
                        self.assertEqual(second.get('result'), 'Synthetic followup.', second)
                        proc.stdin.close(); proc.wait(timeout=10)
                        self.assertEqual(proc.returncode, 0, proc.stderr.read())
                    finally:
                        if proc.poll() is None: proc.kill(); proc.wait()
                        reader.join(timeout=1)
                        proc.stdout.close(); proc.stderr.close()
                    self.assertEqual(errors, [])
                    self.assertEqual(len(seen), 4)
                    results = [b for doc in seen[1:] for msg in doc['messages']
                               if msg['role'] == 'user' and isinstance(msg['content'], list)
                               for b in msg['content'] if b.get('type') == 'tool_result']
                    self.assertTrue(any(b['tool_use_id'] == 'read_fixture' and 'synthetic fixture value' in json.dumps(b) for b in results))
                    self.assertTrue(any(b['tool_use_id'] == 'bash_fixture' and 'synthetic bash value' in json.dumps(b) for b in results), results)
                    for doc in seen:
                        self.assertEqual(doc['model'], MODEL)
                        self.assertEqual(doc['thinking'], {'type': 'adaptive'})
                        self.assertEqual({t['name'] for t in doc['tools']}, {'Read', 'Bash'})
                    self.assertEqual(len(upstream_server.requests), 4)


if __name__ == '__main__': unittest.main()
