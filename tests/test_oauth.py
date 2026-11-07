"""Focused OAuth boundary regressions. Doubles are not native model evidence."""
import contextlib
import importlib.util
import io
import http.client
from http.server import HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlencode, urlsplit
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('botsesh_oauth_test',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package;spec.loader.exec_module(package)
from botsesh_oauth_test import oauth
from botsesh_oauth_test.client import BotSeshError
from botsesh_oauth_test.storage import Store,StorageError

class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)/'hermes';self.home.mkdir(mode=0o700)
        self.env=patch.dict(os.environ,{'HERMES_HOME':str(self.home)});self.env.start()
        # Abort rather than let any fixture touch the active operator home.
        self.assertEqual(oauth._auth_path(),self.home/'auth.json')
        self.store=Store(self.home/'plugin-data/botsesh')
        self.client=oauth.OAuthClient(self.store)
        self.binding={'room_id':'one','origin':'https://botsesh.com','url':'https://botsesh.com/b/one','name':'One','participant_id':'bot','oauth_id':'owned','transport':'oauth','active':True,'state':'connected'}
        self.store.update(lambda d:d['rooms'].update(one=self.binding))
        self.row={'id':'owned','access_token':'A'*43,'refresh_token':'R'*43,'expires_at_ms':int(time.time()*1000)+300000,'extra':{'origin':'https://botsesh.com','room_id':'one','client_id':oauth.CLIENT_ID}}
        oauth._save(self.row)
    def tearDown(self):
        self.env.stop();self.tmp.cleanup()
    def test_listener_reports_exact_membership_state_without_read_effect(self):
        with patch.object(self.client, 'rpc', return_value={'read_effect': 'none'}) as rpc:
            self.client.call(self.binding, 'listener', state='listening')
            rpc.assert_called_once_with(self.binding, 'hermes_listener', {'room_id': 'one', 'state': 'listening'})
            self.client.call(self.binding, 'listener', state='stopped')
            self.assertEqual(rpc.call_args.args[2], {'room_id': 'one', 'state': 'stopped'})
            with self.assertRaises(BotSeshError):
                self.client.call(self.binding, 'listener', state='invented')
    def test_profile_store_is_private_and_never_inherits_global_pool(self):
        with patch('hermes_cli.auth._load_global_auth_store',side_effect=AssertionError('global fallback forbidden')):
            self.assertEqual(oauth._rows()[0]['id'],'owned')
        self.assertEqual(self.home.joinpath('auth.json').stat().st_mode&0o777,0o600)
        self.home.joinpath('auth.json').chmod(0o644)
        with self.assertRaises(StorageError):oauth._rows()
    def test_refresh_is_serialized_across_concurrent_clients(self):
        oauth._save({**self.row,'expires_at_ms':0})
        def rotate(*args):
            time.sleep(.02)
            return {'access_token':'B'*43,'refresh_token':'S'*43,'expires_in':300}
        with patch.object(oauth,'_token_request',side_effect=rotate) as token:
            with ThreadPoolExecutor(max_workers=2) as pool:
                values=list(pool.map(lambda _:self.client._access(self.binding),range(2)))
        self.assertEqual(values,['B'*43]*2);self.assertEqual(token.call_count,1)
        self.assertEqual(oauth._rows()[0]['refresh_token'],'S'*43)
    def test_read_requests_complete_command_rich_history_before_size_fence(self):
        value={'messages':[{'number':0,'text':'exact','senderParticipantId':'human','authorParticipant':{'id':'human','verified':True}}], 'batch_id':str(uuid.uuid4()),'ack_eligible':True,'has_more':False,'truncated':False,'read_checkpoint':0,'next_checkpoint':1}
        def catchup(binding,name,args=None):
            self.assertEqual(name,'catchup')
            # Real production command history needed >60k before the server's
            # compact projection. The plugin's own final160k fence stays intact.
            self.assertGreaterEqual(args['max_chars'],150000)
            return value
        with patch.object(self.client,'rpc',side_effect=catchup):
            read=self.client.call(self.binding,'read',target_number=0)
        self.assertEqual(read['batch']['messages'][0]['text'],'exact')
        self.assertEqual(read['briefing'][0],value)
        self.assertEqual(read['read_effect'],'none')

    def test_null_reply_preview_and_unprocessed_checks_do_not_ack(self):
        result={'messages':[{'number':0,'text':'exact','senderParticipantId':'human','authorParticipant':{'id':'human','verified':True},'mentions':[{'participant':None},{'participant':{'id':'bot'}}],'replyPreview':None}], 'batch_id':str(uuid.uuid4()),'ack_eligible':True,'has_more':False,'truncated':False,'read_checkpoint':0,'next_checkpoint':1}
        with patch.object(self.client,'rpc',side_effect=lambda binding,name,args=None: {'targets':[{'number':0}], 'has_more':False, 'backlog':1} if name=='hermes_inbox' else result) as rpc:
            checked=self.client.call(self.binding,'check')
            self.assertEqual(checked['mentions'],[0]);self.assertEqual(checked['replies'],[])
            self.assertNotIn('oauth_receipts',self.store.room('one'))
            read=self.client.call(self.binding,'read')
            self.assertEqual(read['read_effect'],'none')
            self.assertEqual([c.args[1] for c in rpc.call_args_list],['hermes_inbox','catchup'])
    def test_foreign_receipts_and_changed_retry_fail_before_network(self):
        with patch.object(self.client,'rpc') as rpc:
            with self.assertRaises(BotSeshError):self.client.call(self.binding,'post',text='exact',client_id=str(uuid.uuid4()),receipt_id=str(uuid.uuid4()),processed=True)
            rpc.assert_not_called()
        client_id=str(uuid.uuid4());exact={'text':'exact','client_id':client_id,'receipt_id':str(uuid.uuid4()),'processed':True}
        self.store.update(lambda d:d['rooms']['one'].update(oauth_posts={client_id:{'exact':exact,'complete':True,'result':{'number':5}}}))
        with patch.object(self.client,'rpc',return_value={'rooms':[{'room_id':'one','participant_id':'bot'}]}) as rpc:
            self.assertEqual(self.client.call(self.binding,'post',**exact),{'number':5})
            self.assertEqual(rpc.call_args.args[1],'list_rooms')
            with self.assertRaises(BotSeshError):self.client.call(self.binding,'post',**{**exact,'text':'changed'})
    def test_disconnect_verifies_denial_before_removing_token(self):
        with patch.object(oauth,'_http',return_value={}) as revoke,patch.object(self.client,'rpc',side_effect=BotSeshError('invalid_token',401)):
            self.client.disconnect('one')
        self.assertEqual(revoke.call_args.args[:2],('https://botsesh.com','/oauth/revoke'))
        self.assertEqual(oauth._rows(),[]);self.assertEqual(self.store.read()['rooms']['one']['state'],'revoked')
        self.client.disconnect('one')  # idempotent cleanup after remote revocation
    def test_closed_dm_disconnect_still_revokes_the_live_source_grant(self):
        self.store.update(lambda d:d['rooms'].update(dm={**self.binding,'room_id':'dm','oauth_root':'one','active':False,'state':'membership_revoked'}))
        with patch.object(oauth,'_http',return_value={}) as revoke,patch.object(self.client,'rpc',side_effect=BotSeshError('invalid_token',401)):
            self.client.disconnect('dm')
        revoke.assert_called_once()
        self.assertEqual(oauth._rows(),[])
        self.assertTrue(all(room['state']=='revoked' for room in self.store.read()['rooms'].values()))
    def test_invalid_state_cannot_exchange_a_code(self):
        # Exercise the native helper's real state comparison with only its
        # callback transport doubled. No token exchange may happen on mismatch.
        from hermes_cli.auth_oauth_pkce_plugin import login,OAuthPKCEConfig
        from hermes_cli.auth_constants import AuthError
        config=OAuthPKCEConfig(client_id=oauth.CLIENT_ID,authorize_url='https://botsesh.com/oauth/authorize',token_url='https://botsesh.com/oauth/token',token_request=lambda fields:self.fail('state mismatch exchanged a code'))
        from unittest.mock import Mock
        server=Mock(server_address=('127.0.0.1',43111))
        with contextlib.redirect_stdout(io.StringIO()), patch('hermes_cli.auth_device_flow._bind_loopback_callback_server',return_value=server),patch('hermes_cli.auth_device_flow._serve_loopback_callback',return_value={'state':'foreign-state','code':'not-a-real-code'}):
            with self.assertRaises(AuthError) as raised:login('botsesh',config,open_browser=False)
        self.assertEqual(raised.exception.code,'oauth_state_mismatch')

class LoopbackRedirectTests(unittest.TestCase):
    def request_callback(self, path, *, origin='https://botsesh.com', expected_state='private-state'):
        handler, result = oauth._make_loopback_callback_handler('/callback', origin=origin, expected_state=expected_state)
        with HTTPServer(('127.0.0.1', 0), handler) as server:
            thread = Thread(target=server.handle_request, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            try:
                connection.request('GET', path)
                response = connection.getresponse()
                status, headers, body = response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()
                thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        return status, headers, body, result

    def test_idle_browser_preconnect_does_not_block_callback_or_shutdown(self):
        import socket
        handler, result = oauth._make_loopback_callback_handler(
            '/callback', origin='https://botsesh.com', expected_state='private-state')
        with HTTPServer(('127.0.0.1', 0), handler) as server:
            serving = Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
            serving.start()
            idle = socket.create_connection(server.server_address, timeout=1)
            connection = http.client.HTTPConnection(*server.server_address, timeout=4)
            try:
                # Chromium opens sockets before sending an HTTP request. The
                # native single-threaded HTTPServer must not wait indefinitely.
                time.sleep(.1)
                connection.request('GET', '/callback?code=private-code&state=private-state')
                response = connection.getresponse()
                self.assertEqual(response.status, 302)
                response.read()
                self.assertEqual(result['code'], 'private-code')
            finally:
                idle.close()
                connection.close()
                server.shutdown()
                serving.join(timeout=2)
        self.assertFalse(serving.is_alive())

    def test_success_captures_callback_but_redirects_without_oauth_values(self):
        with patch('sys.stderr') as stderr:
            status, headers, body, result = self.request_callback(
                '/callback?' + urlencode({'code': 'private-code', 'state': 'private-state'}))
        self.assertEqual(status, 302)
        self.assertEqual(headers['Location'], 'https://botsesh.com/connected?client=hermes')
        self.assertEqual(result, {'code': 'private-code', 'state': 'private-state',
                                  'error': None, 'error_description': None})
        self.assertNotIn(b'private-', body)
        stderr.write.assert_not_called()


    def test_error_redirect_does_not_reflect_description_or_state(self):
        status, headers, body, result = self.request_callback('/callback?' + urlencode({
            'error': 'access_denied', 'error_description': 'private-description', 'state': 'private-state'}))
        self.assertEqual(status, 302)
        self.assertEqual(headers['Location'], 'https://botsesh.com/connected?client=hermes&error=1')
        self.assertEqual(result['error_description'], 'private-description')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(body, b'')

    def test_local_origin_is_preserved_and_query_cannot_choose_redirect(self):
        for query in ('code=one&state=two&next=https://evil.test', 'error=denied&state=two'):
            with self.subTest(query=query):
                status, headers, _, _ = self.request_callback('/callback?' + query, origin='http://127.0.0.1:43210', expected_state='two')
                self.assertEqual(status, 302)
                self.assertEqual(headers['Location'], 'http://127.0.0.1:43210/connected?client=hermes'
                                 + ('&error=1' if query.startswith('error=') else ''))

    def test_wrong_path_does_not_capture_callback(self):
        status, headers, _, result = self.request_callback('/other?code=private-code&state=private-state')
        self.assertEqual(status, 404)
        self.assertNotIn('Location', headers)
        self.assertTrue(all(value is None for value in result.values()))

    def test_empty_callback_does_not_complete_native_wait(self):
        _, headers, _, result = self.request_callback('/callback?state=private-state')
        self.assertTrue(headers['Location'].endswith('&error=1'))
        self.assertIsNone(result['code'])
        self.assertIsNone(result['error'])

    def test_redirect_origin_rejects_arbitrary_hosts_and_url_components(self):
        for origin in ('https://evil.test', 'https://botsesh.com.evil.test', 'http://localhost:1234',
                       'http://127.0.0.1:1234/evil', 'http://127.0.0.1:1234?next=evil',
                       'http://user@127.0.0.1:1234', 'https://botsesh.com#evil'):
            with self.subTest(origin=origin), self.assertRaises(BotSeshError):
                oauth._make_loopback_callback_handler('/callback', origin=origin, expected_state='private-state')


class LoginRedirectTests(unittest.TestCase):
    """Real native PKCE/bind + plugin handler; browser/token/serve transport doubles."""
    def setUp(self):
        from hermes_cli import auth_device_flow
        self.native = auth_device_flow
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.output = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.browser = self.stack.enter_context(patch('webbrowser.open'))
        self.hint = self.stack.enter_context(patch.object(self.native, '_print_loopback_ssh_hint', autospec=True))
        self.graphical = self.stack.enter_context(patch.object(self.native, '_can_open_graphical_browser', autospec=True, return_value=True))
        self.exchange = self.stack.enter_context(patch.object(oauth, '_token_request', return_value={
            'access_token': 'A'*43, 'refresh_token': 'R'*43, 'expires_in': 300}))
        self.callback_overrides = {}
        self.serve = self.stack.enter_context(patch.object(self.native, '_serve_loopback_callback',
                                                         autospec=True, side_effect=self.serve_callback))

    def serve_callback(self, server, result, *, timeout_seconds, err, timeout_code):
        self.authorize_url = next(line.strip() for line in self.output.getvalue().splitlines()
                                  if '/oauth/authorize?' in line)
        self.params = {key: values[0] for key, values in parse_qs(urlsplit(self.authorize_url).query).items()}
        fields = {'code': 'private-code', 'state': self.params['state'], **self.callback_overrides}
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        try:
            connection.request('GET', '/callback?' + urlencode(fields))
            response = connection.getresponse()
            self.response = response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()
            thread.join(timeout=3)
            server.server_close()
        return result

    def test_login_uses_plugin_redirect_and_native_pkce_with_os_browser(self):
        grant = oauth.login(room_hint='room-one', timeout=42)
        self.assertEqual(self.response[0], 302)
        self.assertEqual(self.response[1]['Location'], 'https://botsesh.com/connected?client=hermes')
        self.browser.assert_called_once_with(self.authorize_url)
        self.hint.assert_called_once_with(self.params['redirect_uri'])
        self.assertEqual(self.serve.call_args.kwargs['timeout_seconds'], 42)
        self.assertEqual(self.params['client_id'], oauth.CLIENT_ID)
        self.assertEqual(self.params['scope'], ' '.join(oauth.SCOPES))
        self.assertEqual(self.params['resource'], 'https://botsesh.com/mcp')
        self.assertEqual(self.params['room_hint'], 'room-one')
        self.assertEqual(self.params['code_challenge_method'], 'S256')
        origin, fields = self.exchange.call_args.args
        self.assertEqual(origin, 'https://botsesh.com')
        self.assertEqual(fields['grant_type'], 'authorization_code')
        self.assertEqual(fields['code'], 'private-code')
        self.assertEqual(fields['redirect_uri'], self.params['redirect_uri'])
        self.assertEqual(self.params['code_challenge'], self.native._pkce_code_challenge(fields['code_verifier']))
        self.assertEqual(grant['access_token'], 'A'*43)
        self.assertNotIn('private-code', self.output.getvalue())
        self.assertNotIn('A'*43, self.output.getvalue())


    def test_invalid_state_never_exchanges_and_uses_constant_time_comparison(self):
        import hmac
        from hermes_cli.auth_constants import AuthError
        self.callback_overrides['state'] = 'foreign-state'
        with patch('hmac.compare_digest', wraps=hmac.compare_digest) as compare:
            with self.assertRaises(AuthError) as raised:
                oauth.login(open_browser=False)
        self.assertEqual(raised.exception.code, 'oauth_state_mismatch')
        compare.assert_any_call('foreign-state', self.params['state'])
        self.assertEqual(compare.call_count, 2)
        self.assertTrue(self.response[1]['Location'].endswith('&error=1'))
        self.exchange.assert_not_called()
        self.browser.assert_not_called()

    def test_denial_redirects_to_error_without_exchange_or_error_prose(self):
        from hermes_cli.auth_constants import AuthError
        self.callback_overrides.update(error='access_denied', error_description='private-description')
        with self.assertRaises(AuthError) as raised:
            oauth.login()
        self.assertEqual(raised.exception.code, 'oauth_authorization_denied')
        self.assertEqual(self.response[1]['Location'], 'https://botsesh.com/connected?client=hermes&error=1')
        self.assertNotIn('private-description', str(raised.exception))
        self.exchange.assert_not_called()

    def test_local_login_redirects_to_configured_origin_without_opening_headless_browser(self):
        self.graphical.return_value = False
        oauth.login(origin='http://127.0.0.1:43210')
        self.assertEqual(self.response[1]['Location'], 'http://127.0.0.1:43210/connected?client=hermes')
        self.assertEqual(self.params['resource'], 'http://127.0.0.1:43210/mcp')
        self.assertEqual(self.exchange.call_args.args[0], 'http://127.0.0.1:43210')
        self.browser.assert_not_called()
        self.hint.assert_called_once_with(self.params['redirect_uri'])

    def test_browser_failure_still_accepts_manual_callback(self):
        self.browser.side_effect = RuntimeError('private-browser-error')
        oauth.login()
        self.exchange.assert_called_once()
        self.assertNotIn('private-browser-error', self.output.getvalue())

    def test_no_code_is_rejected_before_exchange(self):
        from hermes_cli.auth_constants import AuthError
        self.callback_overrides['code'] = ''
        with self.assertRaises(AuthError) as raised:
            oauth.login(open_browser=False)
        self.assertEqual(raised.exception.code, 'oauth_no_code')
        self.exchange.assert_not_called()

    def test_cancel_and_timeout_close_listener_without_exchange(self):
        from hermes_cli.auth_constants import AuthError
        bind = self.native._bind_loopback_callback_server
        for failure, expected in ((KeyboardInterrupt(), SystemExit),
                                  (AuthError('timed out', provider='botsesh', code='oauth_callback_timeout'), AuthError)):
            with self.subTest(failure=type(failure).__name__):
                servers = []
                def record_bind(*args, **kwargs):
                    server = bind(*args, **kwargs)
                    servers.append(server)
                    return server
                self.serve.side_effect = failure
                with patch.object(self.native, '_bind_loopback_callback_server', autospec=True, side_effect=record_bind):
                    with self.assertRaises(expected) as raised:
                        oauth.login(open_browser=False)
                self.assertEqual(servers[0].fileno(), -1)
                self.assertEqual(raised.exception.code, 130 if expected is SystemExit else 'oauth_callback_timeout')
        self.exchange.assert_not_called()

    def test_missing_or_changed_helpers_fall_back_before_starting_plugin_flow(self):
        from hermes_cli import auth_oauth_pkce_plugin as pkce
        def incompatible(*, new_required):
            self.fail('incompatible helper was invoked')
        names = ('_bind_loopback_callback_server', '_pkce_code_verifier', '_pkce_code_challenge',
                 '_print_loopback_ssh_hint', '_serve_loopback_callback', '_can_open_graphical_browser')
        for module, name in [(self.native, name) for name in names] + [(pkce, '_post_token')]:
            for mode in ('missing', 'changed'):
                with self.subTest(helper=name, mode=mode), contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(module, name, incompatible))
                    if mode == 'missing':
                        delattr(module, name)
                    fallback = stack.enter_context(patch.object(pkce, 'login', return_value={'native': True}))
                    handler = stack.enter_context(patch.object(oauth, '_make_loopback_callback_handler'))
                    self.assertEqual(oauth.login(open_browser=False, timeout=17), {'native': True})
                    handler.assert_not_called()
                    fallback.assert_called_once()
                    args, kwargs = fallback.call_args
                    self.assertEqual(args[0], 'botsesh')
                    self.assertEqual(args[1].timeout_seconds, 17)
                    self.assertEqual(args[1].extra_authorize_params['resource'], 'https://botsesh.com/mcp')
                    self.assertEqual(kwargs, {'open_browser': False})
        self.exchange.assert_not_called()


if __name__=='__main__':unittest.main()
