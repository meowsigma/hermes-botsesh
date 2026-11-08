"""Loopback callback diagnostics and phone input; never log credential values."""
from __future__ import annotations
import hmac

from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

FIELDS = ('code', 'state', 'error', 'error_description')


def callback_handler(expected_path, *, landing, expected_state):
    result = dict.fromkeys(FIELDS)
    observations = []

    class CallbackHandler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def _callback(self):
            parsed = urlsplit(self.path)
            params = parse_qs(parsed.query, keep_blank_values=True)
            malformed = False
            if self.command == 'POST':
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 <= size <= 8192:
                        raise ValueError
                    if size:
                        if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                            raise ValueError
                        body = parse_qs(self.rfile.read(size).decode('utf-8'), keep_blank_values=True)
                        for key, values in body.items():
                            params.setdefault(key, []).extend(values)
                except (ValueError, OSError):
                    malformed = True
            # No query/body values or untrusted error descriptions in diagnostics.
            observations.append({'method': self.command, 'path': parsed.path,
                                 'present': {key: key in params for key in FIELDS}})
            del observations[:-20]
            if parsed.path != expected_path:
                self.send_error(404)
                return
            if malformed or any(len(params.get(key, [])) > 1 for key in FIELDS):
                self.send_error(400)
                return
            result.update({key: params.get(key, [None])[0] for key in FIELDS})
            failed = result['error'] or not str(result['code'] or '').strip() or not hmac.compare_digest(str(result['state'] or ''), expected_state)
            self.send_response(302)
            self.send_header('Location', landing + ('&error=1' if failed else ''))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Length', '0')
            self.end_headers()

        do_GET = _callback
        do_POST = _callback

        def log_message(self, format, *args):
            pass

    CallbackHandler.observations = observations
    return CallbackHandler, result


def timeout_diagnostic(observations):
    print('OAuth callback timed out. Received (latest 20 requests; values omitted):')
    if not observations:
        print('  No HTTP requests received.')
    for item in observations:
        # JSON escaping prevents a request path from injecting terminal control sequences.
        import json
        path = json.dumps(item['path'][:200], ensure_ascii=True)
        presence = ', '.join(f'{key}={"present" if value else "absent"}' for key, value in item['present'].items())
        print(f'  {item["method"]} path={path}; {presence}')
    print('Next: run `hermes botsesh connect --no-browser` again (300s default). Use SSH loopback forwarding, or run `hermes botsesh connect --no-browser --paste-redirect` for a private redirect-URL prompt. Type url there to re-print the authorization URL.')


def paste_callback(*, redirect_uri, authorize_url, expected_state, timeout, err):
    """Explicit phone mode: hidden TTY input, bounded by the same login deadline.

    No background stdin reader can linger after a network callback and steal the
    operator's next command. Non-TTY input is refused instead of echoing secrets.
    """
    import sys
    import time
    if not sys.stdin.isatty():
        raise err('paste mode requires a private terminal.', 'oauth_paste_requires_tty')
    try:
        import termios
        import select
    except ImportError:
        raise err('use SSH loopback forwarding on this platform.', 'oauth_paste_not_supported') from None
    print('Phone fallback: paste this attempt’s full redirect URL (hidden), or type url to re-print the authorization URL.')
    deadline = time.monotonic() + timeout
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    hidden = list(original)
    hidden[3] &= ~termios.ECHO
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, hidden)
        while True:
            print('Redirect URL / url: ', end='', flush=True)
            remaining = max(0, deadline - time.monotonic())
            if not select.select([sys.stdin], [], [], remaining)[0]:
                raise err('callback timed out.', 'oauth_callback_timeout')
            line = sys.stdin.readline()
            if not line:
                raise err('paste input closed.', 'oauth_paste_closed')
            value = line.strip()
            print()
            if value.lower() == 'url':
                print(authorize_url)
                continue
            parsed, expected = urlsplit(value), urlsplit(redirect_uri)
            fields = parse_qs(parsed.query, keep_blank_values=True)
            if (parsed.scheme != expected.scheme or parsed.netloc != expected.netloc or parsed.path != expected.path
                    or parsed.fragment or len(fields.get('state', [])) != 1
                    or any(len(fields.get(k, [])) > 1 for k in FIELDS)
                    or not hmac.compare_digest(fields.get('state', [''])[0], expected_state)
                    or not (fields.get('code', [''])[0] or fields.get('error', [''])[0])):
                print('Redirect rejected: use this attempt’s exact loopback URL with code/error and matching state. No values logged.')
                continue
            return {key: fields.get(key, [None])[0] for key in FIELDS}
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)
