"""Focused OAuth boundary regressions. Doubles are not native model evidence."""
import importlib.util
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
    def test_null_reply_preview_and_unprocessed_checks_do_not_ack(self):
        result={'messages':[{'number':0,'text':'exact','senderParticipantId':'human','authorParticipant':{'id':'human','verified':True},'mentions':[{'participant':None},{'participant':{'id':'bot'}}],'replyPreview':None}], 'batch_id':str(uuid.uuid4()),'ack_eligible':True,'has_more':False,'truncated':False,'read_checkpoint':0,'next_checkpoint':1}
        with patch.object(self.client,'rpc',return_value=result) as rpc:
            checked=self.client.call(self.binding,'check')
            self.assertEqual(checked['mentions'],[0]);self.assertEqual(checked['replies'],[])
            self.assertNotIn('oauth_receipts',self.store.room('one'))
            read=self.client.call(self.binding,'read')
            self.assertEqual(read['read_effect'],'none')
            self.assertEqual([c.args[1] for c in rpc.call_args_list],['catchup','catchup'])
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
    def test_invalid_state_cannot_exchange_a_code(self):
        # Exercise the native helper's real state comparison with only its
        # callback transport doubled. No token exchange may happen on mismatch.
        from hermes_cli.auth_oauth_pkce_plugin import login,OAuthPKCEConfig
        from hermes_cli.auth_constants import AuthError
        config=OAuthPKCEConfig(client_id=oauth.CLIENT_ID,authorize_url='https://botsesh.com/oauth/authorize',token_url='https://botsesh.com/oauth/token',token_request=lambda fields:self.fail('state mismatch exchanged a code'))
        from unittest.mock import Mock
        server=Mock(server_address=('127.0.0.1',43111))
        with patch('hermes_cli.auth_device_flow._bind_loopback_callback_server',return_value=server),patch('hermes_cli.auth_device_flow._serve_loopback_callback',return_value={'state':'foreign-state','code':'not-a-real-code'}):
            with self.assertRaises(AuthError) as raised:login('botsesh',config,open_browser=False)
        self.assertEqual(raised.exception.code,'oauth_state_mismatch')

if __name__=='__main__':unittest.main()
