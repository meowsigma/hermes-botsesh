"""Native UI copy only. No model, daemon, network or approval substitution."""
from unittest.mock import patch
from test_solid import SolidTests as _SolidTests
from gateway.platforms.event import ProcessingOutcome
from gateway.run import GatewayRunner


import unittest


class NativeCopyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = _SolidTests.asyncSetUp
    make_adapter = _SolidTests.make_adapter
    async def test_core_shutdown_status_is_suppressed_before_one_linked_failure(self):
        await self.adapter._check_room('a')
        event = self.adapter._admit_event.call_args.args[0]
        current = self.adapter._current['a']
        with patch('agent.i18n.t', side_effect=lambda key: key):
            result = await GatewayRunner._send_notice_logged(
                self.adapter, 'a', 'gateway.shutdown.notice_shutdown', 'botsesh', '%s %s %s')
        self.assertTrue(result)
        self.assertEqual(self.posts, {})
        self.assertFalse(current['model_processed'])
        self.assertNotIn('provider_hint', current)
        await self.adapter.on_processing_complete(event, ProcessingOutcome.CANCELLED)
        self.assertEqual(len(self.posts), 1)
        payload = next(iter(self.posts.values()))
        self.assertEqual(payload['reply_to'], 3)
        self.assertNotIn('gateway.', payload['text'])
        self.assertNotIn('processed', payload)
        failed = [a for _, op, a in self.calls if op == 'delivery' and a.get('state') == 'failed']
        self.assertEqual(failed[-1]['notice_client_id'], payload['client_id'])
        await self.make_adapter()._flush_failures('a')
        self.assertEqual(len(self.posts), 1)

    async def test_localized_shutdown_is_suppressed_but_ordinary_authored_text_is_exact(self):
        await self.adapter._check_room('a')
        with patch('agent.i18n.t', return_value='Hermes wird beendet.'):
            await self.adapter.send('a', 'Hermes wird beendet.', metadata={'_interim_send': True})
        self.assertFalse(self.posts)
        text = 'Literal example: gateway.shutdown.notice_shutdown'
        self.adapter._current['a']['model_processed'] = True
        self.assertTrue((await self.adapter.send('a', text, metadata={'notify': True})).success)
        self.assertEqual(next(iter(self.posts.values()))['text'], text)

    async def test_idle_shutdown_uses_core_translation_or_english_fallback(self):
        for key in ('gateway.shutdown.notice_shutdown', 'gateway.shutdown.notice_restart'):
            with patch('agent.i18n.t', side_effect=lambda key: key):
                self.assertTrue((await self.adapter.send('a', key, metadata={'_interim_send': True})).success)
        self.assertEqual(self.posts, {})  # Runtime notices stay local, including idle shutdown.

    async def test_native_setup_notice_has_fallback_and_formatted_localized_copy(self):
        with patch('agent.i18n.t', side_effect=lambda key, **fields: key):
            self.assertTrue((await self.adapter.send('a', 'gateway.notify.no_home_channel')).success)
        self.assertEqual(self.posts, {})
        with patch('agent.i18n.t', return_value='Localized BotSesh /sethome') as translator:
            await self.adapter.send('a', 'gateway.notify.no_home_channel')
            translator.assert_called_once_with('gateway.notify.no_home_channel', platform='BotSesh', sethome_cmd='/sethome')
        self.assertEqual(self.posts, {})

    async def test_unknown_native_notice_falls_back_but_final_authored_bytes_are_not_translated(self):
        with patch('agent.i18n.t', side_effect=lambda key: key):
            await self.adapter.send('a', 'gateway.future.notice', metadata={'_interim_send': True})
        self.assertEqual(self.posts, {})
        self.posts.clear()
        await self.adapter._check_room('a')
        self.adapter._current['a']['model_processed'] = True
        await self.adapter.send('a', 'gateway.future.notice', metadata={'notify': True})
        self.assertEqual(next(iter(self.posts.values()))['text'], 'gateway.future.notice')
