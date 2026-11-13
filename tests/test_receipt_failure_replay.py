"""Replay the captured threaded-post refusal, never an inference request."""
import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_plugin import Client, Store, tools
from hermes_botsesh_test.invitation import InvitationClient

FIXTURES = Path(__file__).parent / 'fixtures' / 'agents11d'

def capture(n):
    with gzip.open(FIXTURES / f'call-{n:02d}-request.json.gz', 'rt') as stream:
        return json.load(stream)


class ReceiptFailureReplay(unittest.TestCase):
    def test_exact_captured_post_is_rejected_with_actionable_no_read_exit(self):
        second = capture(3)
        call = next(c for m in second['native_messages'] if m['role']=='assistant' for c in m.get('tool_calls', []))
        args = json.loads(call['function']['arguments'])
        old = json.loads(next(m['content'] for m in second['native_messages'] if m['role']=='tool'))
        room = args['room_id']
        before = copy.deepcopy(args)
        with tempfile.TemporaryDirectory() as temp:
            client = Client(Store(Path(temp) / 'plugin'))
            binding = dict(room_id=room, active=True, transport='invitation', participant_id='fixture-seat', origin='https://botsesh.com')
            client.store.update(lambda d:d['rooms'].update({room:binding}))
            # Only credential/access HTTP is doubled. Actual tools -> Client ->
            # InvitationClient -> OAuth send journal -> fail-closed RPC runs.
            with patch.object(tools, 'Client', return_value=client), \
                 patch.object(tools, 'calling_scope', return_value=('local', '')), \
                 patch.object(InvitationClient, '_access', return_value='fixture-not-a-secret'), \
                 patch.object(InvitationClient, 'verify', return_value={'readCheckpoint':0}), \
                 patch.object(InvitationClient, 'rest', side_effect=AssertionError('No HTTP mutation allowed')):
                result = json.loads(tools.guarded(tools.post)(args, ctx=Mock()))
            self.assertEqual(result['error'], old['error'])
            self.assertIs(result['ok'], False)
            self.assertNotIn('pending', result)
            self.assertIn('omit reply_to', result.get('message', ''))
            self.assertIn('processed:true', result['message'])
            self.assertIn('receipt_id', result['message'])
            self.assertIn('client_id', result['message'])
            saved = client.store.room(room)
            self.assertNotIn('read_attested', saved)
            self.assertNotIn('processed_cursor', saved)
            self.assertFalse(any(r.get('complete') for r in saved.get('oauth_posts', {}).values()))
        self.assertEqual(args, before, 'Never repair captured authored arguments')
        post = next(s for s in tools.SCHEMAS if s['name']=='botsesh_post')
        self.assertIn('processed:true', post['parameters']['properties']['reply_to'].get('description',''))


if __name__ == '__main__':
    unittest.main()
