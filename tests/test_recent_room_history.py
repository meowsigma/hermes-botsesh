"""Offline native-admission projection; live recall is a separate real-model proof."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('history_plugin_test',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package;spec.loader.exec_module(package)
from history_plugin_test.client import Client
from history_plugin_test.storage import Store
from history_plugin_test.oauth import OAuthClient

class RecentHistoryTests(unittest.IsolatedAsyncioTestCase):
    def test_grant_wide_exact_pages_without_room_args_do_not_crash_or_invent_a_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=OAuthClient(Store(Path(tmp)/'private'))
            binding={'origin':'https://botsesh.com'}
            def http(origin,path,**kwargs):
                payload=kwargs['payload'];params=payload['params']
                if params['name']=='list_rooms':
                    self.assertEqual(params['arguments'],{})
                    data={'next_cursor':'exact-page'}
                else:
                    self.assertEqual(params,{'name':'read_tool_page','arguments':{'cursor':'exact-page'}})
                    data={'json_fragment':'{"rooms":[]}','next_cursor':None}
                return {'jsonrpc':'2.0','id':payload['id'],'result':{'structuredContent':data}}
            with patch('history_plugin_test.oauth._http',side_effect=http) as transport:
                self.assertEqual(client.rpc(binding,'list_rooms',token='test-only'),{'rooms':[]})
                self.assertEqual(transport.call_count,2)

    async def test_message_three_from_second_human_keeps_own_message_one_before_current_input(self):
        from history_plugin_test.adapter import BotSeshAdapter
        from gateway.config import PlatformConfig
        from gateway.platform_registry import PlatformEntry, platform_registry
        platform_registry.register(PlatformEntry(name='botsesh',label='BotSesh',adapter_factory=BotSeshAdapter,check_fn=lambda:True))
        self.addCleanup(platform_registry.unregister,'botsesh')
        with tempfile.TemporaryDirectory() as tmp:
            client=Client(Store(Path(tmp)/'private'))
            binding={'active':True,'room_id':'room','url':'https://botsesh.com/b/room','participant_id':'agent'}
            client.store.update(lambda d:d['rooms'].update(room=binding))
            def message(n,author,text):
                return {'id':n+1,'number':n,'text':text,'createdAt':'2026-11-11T20:00:00.000Z',
                        'senderParticipantId':author,'authorParticipant':{'id':author,'name':'Same name','verified':True},
                        'editCount':0}
            rows=[message(0,'human-one','What should be fixed first?'),
                  message(1,'agent','First fix: handle missing notes.json in load().'),
                  message(2,'human-one','Thanks.'),message(3,'human-two','What did you say should be fixed first?')]
            wire={'messages':rows,'context':{'enabled':True,'revision':1,'markdown':'Review notes.py together.'},
                  'batch_id':'receipt-frozen','read_checkpoint':3,'next_checkpoint':4,
                  'ack_eligible':True,'has_more':False,'truncated':False}
            oauth=OAuthClient(client.store)
            with patch.object(oauth,'rpc',return_value=wire):
                frozen=oauth.call(binding,'read',target_number=3)
            self.assertEqual(frozen['batch']['messages'][1]['isOwn'],True)
            self.assertEqual(frozen['batch']['messages'][3]['isOwn'],False,'same display name cannot impersonate this agent')
            def enrolled(room_id,op,**kwargs):
                self.assertEqual(room_id,'room')
                if op=='check': return {'mentions':[3]}
                self.assertEqual(op,'read');self.assertEqual(kwargs['target_number'],3)
                return frozen
            client.enrolled=Mock(side_effect=enrolled)
            with patch('history_plugin_test.adapter.Client',return_value=client):adapter=BotSeshAdapter(PlatformConfig())
            adapter._is_sender_authorized=lambda *args:True
            adapter._admit_event=AsyncMock(return_value=True)
            await adapter._check_room('room')
            event=adapter._admit_event.call_args.args[0]
            decoder=json.JSONDecoder();envelope,_=decoder.raw_decode(event.text)
            self.assertEqual(envelope['your_earlier_message_numbers'],[1])
            self.assertIn('YOUR earlier room messages',envelope['notice'])
            self.assertEqual(envelope['pending_message_numbers'],[3],'history is not newly pending work')
            self.assertIn(rows[1]['text'],event.text)
            self.assertLess(event.text.index(rows[1]['text']),event.text.index('CURRENT BOTSESH INPUTS (exact text;'))
            self.assertEqual(envelope['briefing'][0],wire)
            self.assertEqual(envelope['read_effect'],'none')
            self.assertEqual([m['number'] for m in envelope['batch']['messages']],[0,1,2,3])
            saved=client.store.room('room')['processing_receipts']['3']
            self.assertEqual(saved['receipt_id'],'receipt-frozen')
            self.assertEqual(saved['frozen'],frozen)
            self.assertEqual(client.store.room('room')['oauth_receipts']['receipt-frozen']['numbers'],[0,1,2,3])
            self.assertEqual(event.source.user_id,'human-two')
            self.assertFalse(client.store.room('room').get('handled'))

if __name__=='__main__': unittest.main()
