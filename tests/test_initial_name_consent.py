"""Regression: optional naming must not revoke or block a verified connection."""
import contextlib
import io
import unittest
from unittest.mock import patch
import test_oauth as original

oauth=original.oauth

class InitialNameConsentTests(unittest.TestCase):
    setUp=original.OAuthTests.setUp
    tearDown=original.OAuthTests.tearDown

    def connect(self, room, rename=None, preserve_name=False):
        def rpc(binding, tool, args=None, **kwargs):
            if tool=='hermes_initialize':return {'profile':'default'}
            if tool=='list_rooms':return {'rooms':[room]}
            if tool=='hermes_name':
                if rename:return rename(room,args)
                raise original.BotSeshError('mcp_operation_denied',rejected=True)
            self.fail(tool)
        login=patch.object(oauth,'login',return_value={'access_token':'B'*43,'refresh_token':'S'*43,'expires_in':300}) if hasattr(oauth,'login') else contextlib.nullcontext()
        http=patch.object(oauth,'_http',return_value={}) if hasattr(oauth,'_http') else contextlib.nullcontext()
        with login,http as revoke,patch.object(self.client,'rpc',side_effect=rpc) as calls,contextlib.redirect_stdout(io.StringIO()) as output:
            binding=self.client.connect(name='Scout',profile='default',preserve_name=preserve_name)
        if revoke:revoke.assert_not_called()
        return binding,calls,output.getvalue()

    def test_profile_default_does_not_rename_an_existing_seat_even_with_name_consent(self):
        room=dict(room_id='two',name='Garden',participant_id='second',display_name='Hermes',gateway_profile='default')
        def rename(*_):self.fail('A profile default must not rename the exact existing seat')
        binding,calls,output=self.connect(room,rename,preserve_name=True)
        self.assertEqual(binding['display_name'],'Hermes')
        self.assertFalse(any(c.args[1]=='hermes_name' for c in calls.call_args_list))

    def test_exact_initial_name_is_verified_without_a_rename(self):
        room=dict(room_id='two',name='Garden',participant_id='second',display_name='Scout',gateway_profile='default')
        binding,calls,output=self.connect(room)
        self.assertEqual(binding['display_name'],'Scout')
        self.assertFalse(any(c.args[1]=='hermes_name' for c in calls.call_args_list))
        self.assertTrue(binding['active'])
        self.assertNotIn('tick',output)

    def test_denied_optional_rename_keeps_unique_server_name_and_grant(self):
        room=dict(room_id='two',name='Garden',participant_id='second',display_name='Scout 2',gateway_profile='default')
        binding,calls,output=self.connect(room)
        self.assertEqual(binding['display_name'],'Scout 2')
        self.assertTrue(binding['active'])
        self.assertIn('Named Scout 2; tick Let it change its own name to rename',output)

    def test_transient_optional_rename_failure_keeps_verified_connection(self):
        room=dict(room_id='two',name='Garden',participant_id='second',display_name='Server name',gateway_profile='default')
        def fail(*_):raise original.BotSeshError('connection_failed_retry_same_intent')
        binding,_,_=self.connect(room,fail)
        self.assertEqual(binding['display_name'],'Server name')
        self.assertTrue(binding['active'])

    def test_permitted_rename_reads_back_the_same_participant(self):
        room=dict(room_id='two',name='Garden',participant_id='second',display_name='Hermes',gateway_profile='default')
        def rename(room,args):
            room['display_name']=args['display_name']
            return {'participant':{'id':'second','name':'Scout'}}
        binding,_,output=self.connect(room,rename)
        self.assertEqual(binding['display_name'],'Scout')
        self.assertNotIn('tick',output)

    def test_lost_rename_response_uses_canonical_name_without_revoke(self):
        room=dict(room_id='two',name='Garden',participant_id='second',display_name='Hermes',gateway_profile='default')
        def lost(room,args):
            room['display_name']='Scout'
            raise original.BotSeshError('connection_failed_retry_same_intent')
        binding,_,_=self.connect(room,lost)
        self.assertEqual(binding['display_name'],'Scout')

    def test_naming_readback_cannot_adopt_foreign_identity_or_revoke_grant(self):
        binding={**self.binding,'profile':'default','display_name':'Scout'}
        self.store.update(lambda d:d['rooms']['one'].update(binding))
        http=patch.object(oauth,'_http') if hasattr(oauth,'_http') else contextlib.nullcontext()
        with http as revoke,patch.object(self.client,'rpc',return_value={'rooms':[dict(binding,participant_id='foreign',display_name='Scout')]}):
            with self.assertRaises(original.BotSeshError):self.client._initial_name(binding,'Scout')
        if revoke:revoke.assert_not_called()
        self.assertEqual(self.store.room('one')['participant_id'],'bot')
        self.assertTrue(self.store.room('one')['active'])

if __name__=='__main__':unittest.main()
