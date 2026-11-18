"""DM P2 x attention seam (B81.49 integration). Only the MCP wire is doubled (the test_final_after_post fixture); no model calls.

A real Hermes run on the merged plugin answered through botsesh_post (the attention description told it to set `done` on a final
answer) and put `MEDIA:` lines in that post: the file never arrived. botsesh_post is text only; in an owner DM a MEDIA: line is
refused locally (no wire call, no journal) so the model re-answers in its final reply, where Hermes turns MEDIA: lines into uploads.
"""
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_final_after_post as fixture
from hermes_botsesh_test import tools
from hermes_botsesh_test.oauth import OAuthClient
from gateway.session_context import set_session_vars, clear_session_vars


class DmPostMediaGuard(unittest.IsolatedAsyncioTestCase):
    begin = fixture.FinalAfterPostTests.begin

    async def asyncSetUp(self):
        await fixture.FinalAfterPostTests.asyncSetUp(self)
        inner = OAuthClient.rpc

        def rpc(api, binding, name, args=None, **kwargs):
            result = inner(api, binding, name, args, **kwargs)
            if name == 'hermes_inbox':
                result = {**result, 'capabilities': {'attention': 1}}
            return result
        patcher = patch.object(OAuthClient, 'rpc', rpc)
        patcher.start()
        self.addCleanup(patcher.stop)
        for adapter in self.adapters.values():
            adapter._refresh_dm_controls = AsyncMock()

    def room(self, room='a', *, dm=True):
        def change(data):
            row = data['rooms'][room]
            row.pop('room_type', None)
            row['server_caps'] = {'attention': 1}
            if dm:
                row['room_type'] = 'dm'
        self.client.store.update(change)

    def post(self, payload, *, room='a', target='3', native=True):
        tokens = set_session_vars(platform='botsesh', chat_id=room, message_id=target) if native else None
        try:
            with patch.object(tools, 'Client', return_value=self.client):
                ctx = Mock()
                ctx.session_surface.return_value = 'botsesh' if native else 'cli'
                return json.loads(tools.guarded(lambda args: tools.post(args, ctx))(payload))
        finally:
            if tokens:
                clear_session_vars(tokens)

    def wire(self):
        return [args for _, n, args in self.requests if n == 'hermes_authored']

    async def test_a_media_line_in_an_owner_dm_post_is_refused_before_any_request(self):
        self.room(dm=True)
        await self.begin()
        before = len(self.requests)
        for text in ('Here it is.\n\nMEDIA:/tmp/report.pdf', 'MEDIA: /tmp/chart.png', 'ok\n   media:/tmp/a.csv\nbye'):
            refused = self.post({'text': text, 'attention': 'done'})
            self.assertEqual((refused['ok'], refused['error']), (False, 'media_not_in_post'), text)
            self.assertIn('final reply', refused['message'])
        self.assertEqual(len(self.requests), before, 'refused locally: no wire call')
        self.assertEqual(self.wire(), [])
        # Nothing was journaled, so the turn can still answer normally and an ordinary post still works.
        self.assertTrue(self.post({'text': 'A plain progress note that mentions MEDIA: only mid-line.'})['ok'])
        self.assertEqual(len(self.wire()), 1)

    async def test_group_rooms_and_local_sessions_are_untouched(self):
        self.room(dm=False)
        await self.begin()
        self.assertTrue(self.post({'text': 'Output follows.\nMEDIA:/tmp/report.pdf'})['ok'], 'a group post is just text')
        self.assertEqual(len(self.wire()), 1)

    async def test_the_attention_description_does_not_invite_a_final_answer_post(self):
        description = tools.post_schema['parameters']['properties']['attention']['description']
        self.assertNotIn('final answer to their request', description)
        self.assertIn('do not use this tool to deliver your final answer', description)
        self.assertIn('MEDIA:', description)
        self.assertEqual(tools.post_schema['parameters']['properties']['attention']['enum'], ['done', 'needs_you', 'choice', 'progress'])


if __name__ == '__main__':
    unittest.main()
