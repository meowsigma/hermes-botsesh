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


def provider_request(origin, method, path, *, payload=None):
    """All OAuth HTTP goes through core's documented credentialed request API."""
    import httpx
    from hermes_cli.auth_constants import AuthError
    from hermes_cli.plugin_provider_requests import ProviderNotSignedIn, credentialed_provider_request
    from .oauth import checked_origin, OAUTH_ID
    checked_origin(origin)
    lifecycle = re.fullmatch(r'/api/agent/rooms/[A-Za-z0-9_-]{32}/lifecycle/(people-invites(?:/[0-9a-fA-F-]{36}/revoke)?|rotate-password|messages)', path)
    if not ((path == '/mcp' and method == 'POST') or (lifecycle and method in ('GET', 'POST'))):
        raise BotSeshError('endpoint_denied')
    try:
        response = credentialed_provider_request(OAUTH_ID, method, origin + path,
            json=payload, headers={'Accept': 'application/json, text/event-stream' if path == '/mcp' else 'application/json'}, timeout=35)
    except ProviderNotSignedIn:
        raise BotSeshError('sign_in_required_run_hermes_auth_add_botsesh_rooms', 401) from None
    except AuthError as error:
        code = error.code if isinstance(error.code, str) and re.fullmatch(r'[a-z0-9_-]{1,80}', error.code) else 'oauth_request_failed'
        raise BotSeshError(code) from None
    except httpx.HTTPError:
        raise BotSeshError('connection_failed_retry_same_intent') from None
    status, raw = response.status, response.body
    if len(raw) > MAX_BYTES:
        raise BotSeshError('response_limit')
    retry = retry_after_ms(response.headers.get('retry-after', '0'))
    try:
        value = decode_response(raw, response.headers.get('content-type', ''),
                                request_id=(payload or {}).get('id') if 200 <= status < 300 else None)
    except (ValueError, TypeError, UnicodeError):
        raise BotSeshError('invalid_server_response' if 200 <= status < 300 else 'request_denied', status, retry) from None
    if not 200 <= status < 300:
        code = value.get('code', value.get('error'))
        raise BotSeshError(code if isinstance(code, str) and re.fullmatch(r'[a-z0-9_-]{1,80}', code) else 'request_denied', status, retry)
    return value
