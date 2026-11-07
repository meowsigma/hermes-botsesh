"""Bounded JSON / MCP-over-SSE framing using Hermes's existing httpx.

A POST response is finite. SSE comments/notifications are not room context,
read acknowledgments, or a reason to create another model turn.
"""
from __future__ import annotations
import json
import re
import time
from email.utils import parsedate_to_datetime

from .client import BotSeshError

MAX_BYTES = 2 * 1024 * 1024


def retry_after_ms(value):
    try:
        seconds = float(value)
        if not 0 <= seconds <= 86400:
            return 0
        return seconds * 1000
    except (ValueError, TypeError):
        try:
            return max(0, min(86400, parsedate_to_datetime(value).timestamp() - time.time())) * 1000
        except (ValueError, TypeError, OverflowError):
            return 0


def sse_values(raw):
    """WHATWG data-line framing, including CRLF, BOM and multi-line JSON.

    No incomplete event is accepted on EOF. HTTP cannot prove its final frame
    was delivered if it did not include the blank-line event delimiter.
    """
    text = raw.decode('utf-8-sig')
    lines = re.split(r'\r\n|\r|\n', text)
    data = []
    for line in lines[:-1]:
        if line == '':
            if data:
                yield json.loads('\n'.join(data))
                data = []
        elif not line.startswith(':'):
            field, _, value = line.partition(':')
            if field == 'data':
                data.append(value[1:] if value.startswith(' ') else value)
    if data:
        raise ValueError('incomplete SSE event')


def decode_response(raw, content_type, *, request_id=None):
    if 'text/event-stream' in content_type.lower():
        values = list(sse_values(raw))
        # MCP progress notifications are not the result of this exact request.
        values = [v for v in values if isinstance(v, dict) and ('result' in v or 'error' in v)
                  and (request_id is None or v.get('id') == request_id)]
        if len(values) != 1:
            raise ValueError('missing or duplicate RPC result')
        value = values[0]
    else:
        value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('object required')
    return value


def post(origin, path, *, form=None, payload=None, bearer=None):
    import httpx
    from .oauth import checked_origin
    checked_origin(origin)
    if path not in ('/oauth/token', '/oauth/revoke', '/mcp'):
        raise BotSeshError('endpoint_denied')
    headers = {'Accept': 'application/json, text/event-stream' if path == '/mcp' else 'application/json'}
    if bearer:
        headers['Authorization'] = 'Bearer ' + bearer
    try:
        with httpx.Client(timeout=35, follow_redirects=False, trust_env=False) as client:
            with client.stream('POST', origin + path, headers=headers, data=form, json=payload) as response:
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > MAX_BYTES:
                        raise BotSeshError('response_limit')
                status = response.status_code
                content_type = response.headers.get('content-type', '')
                retry = retry_after_ms(response.headers.get('retry-after', '0'))
    except httpx.HTTPError:
        raise BotSeshError('connection_failed_retry_same_intent') from None
    if status == 200 and path == '/oauth/revoke':
        return {}
    # Keep the real denial status even when a proxy returns HTML, so revoked
    # credentials stop rather than retrying malformed error prose forever.
    try:
        value = decode_response(raw, content_type, request_id=(payload or {}).get('id') if 200 <= status < 300 else None)
    except (ValueError, TypeError, UnicodeError):
        raise BotSeshError('invalid_server_response' if 200 <= status < 300 else 'request_denied', status, retry) from None
    if not 200 <= status < 300:
        code = value.get('error')
        raise BotSeshError(code if isinstance(code, str) and re.fullmatch(r'[a-z0-9_-]{1,80}', code) else 'request_denied', status, retry)
    return value
