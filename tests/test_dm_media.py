"""DM P2 media (PARITY.md section 5): offline contract tests. Doubles, never native-agent evidence.

Covers byte sniffing (a name never makes bytes an image/audio), inbound owner-DM download into
the gateway media cache with provenance, the per-turn cap, outbound uploads (exact bytes, one
stable UUID per intent, voice only for Ogg/Opus), group rooms staying text-only, and cron files
landing only in the owner DM.
"""
import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_plugin import package, Store, Client, BotSeshError  # noqa: F401  (loads hermes_botsesh_test)
from hermes_botsesh_test import media

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
OGG = b'OggS' + b'\x00' * 40
PDF = b'%PDF-1.4\n%%EOF\n'


class Sniff(unittest.TestCase):
    def test_bytes_decide_the_type_not_the_name(self):
        self.assertEqual(media.sniff(PNG, 'notes.txt'), 'image/png')
        self.assertEqual(media.sniff(OGG, 'x.bin', voice=True), 'audio/ogg')
        self.assertEqual(media.sniff(PDF, 'report.pdf'), 'application/pdf')
        # A media extension on unknown bytes stays opaque.
        self.assertEqual(media.sniff(b'hello world', 'photo.jpg'), 'application/octet-stream')
        self.assertEqual(media.sniff(b'hello world', 'song.mp3'), 'application/octet-stream')
        self.assertEqual(media.sniff(b'a,b\n1,2\n', 'data.csv'), 'text/csv')
        ftyp = b'\x00\x00\x00\x18ftypM4A ' + b'\x00' * 16
        self.assertEqual(media.sniff(ftyp, 'voice.m4a'), 'audio/mp4')
        self.assertEqual(media.sniff(ftyp, 'clip.mp4'), 'video/mp4')
        self.assertEqual(media.sniff(ftyp, 'clip.mp4', voice=True), 'audio/mp4')

    def test_safe_name_matches_server_storage(self):
        self.assertEqual(media.safe_name('../a/b\\c\x00.png'), 'b_c_.png')
        self.assertEqual(media.safe_name(''), 'download')


class Inbound(unittest.IsolatedAsyncioTestCase):
    def client(self, payloads):
        client = Mock()
        def enrolled(room_id, op, **args):
            self.assertEqual((room_id, op), ('dm', 'download_file'))
            data = payloads[args['file_id']]
            return {'data': data, 'sha256': hashlib.sha256(data).hexdigest()}
        client.enrolled = Mock(side_effect=enrolled)
        return client

    async def test_photo_voice_and_document_reach_the_gateway_cache_with_provenance(self):
        with tempfile.TemporaryDirectory() as tmp, patch('gateway.platforms.base.get_hermes_home', return_value=Path(tmp), create=True):
            cached = []
            def cache(data, name, mime):
                path = str(Path(tmp) / ('f%d' % len(cached)))
                Path(path).write_bytes(data)
                cached.append((data, mime))
                return path
            with patch.object(media, '_cache', side_effect=cache):
                messages = [
                    {'number': 4, 'isOwn': False, 'authorParticipant': {'id': 'p1', 'kind': 'human'},
                     'attachments': [{'id': 'f-photo', 'name': 'garden.png', 'size': len(PNG)},
                                     {'id': 'f-doc', 'name': 'notes.pdf', 'size': len(PDF)}]},
                    {'number': 5, 'isOwn': False, 'authorParticipant': {'id': 'p1', 'kind': 'human'},
                     'attachments': [{'id': 'f-voice', 'name': 'voice-1.webm', 'size': len(OGG), 'voice': True}]},
                    {'number': 6, 'isOwn': True, 'attachments': [{'id': 'f-own', 'name': 'mine.png', 'size': 10}]},
                ]
                result = await media.inbound(self.client({'f-photo': PNG, 'f-doc': PDF, 'f-voice': OGG}), 'dm', messages)
        self.assertEqual(result['media_types'], ['image/png', 'application/pdf', 'audio/ogg'])
        self.assertEqual(result['message_type'], 'VOICE')
        self.assertEqual([d for d, _ in cached], [PNG, PDF, OGG])  # exact bytes, own upload skipped
        self.assertEqual([f['provenance'] for f in result['files']], ['room_data'] * 3)
        self.assertEqual(result['files'][2]['voice_note'], True)
        self.assertEqual(result['files'][0]['sha256'], hashlib.sha256(PNG).hexdigest())

    async def test_photos_only_is_a_photo_turn_and_over_cap_files_are_named_not_fetched(self):
        with patch.object(media, '_cache', side_effect=lambda d, n, m: '/tmp/x'):
            big = {'id': 'f-big', 'name': 'huge.png', 'size': media.MAX_MESSAGE_BYTES + 1}
            client = self.client({'f-photo': PNG})
            result = await media.inbound(client, 'dm', [{'number': 1, 'attachments': [{'id': 'f-photo', 'name': 'a.png', 'size': len(PNG)}, big]}])
        self.assertEqual(result['message_type'], 'PHOTO')
        self.assertEqual(client.enrolled.call_count, 1)
        self.assertEqual(result['files'][1]['delivered'], False)

    async def test_a_transient_download_failure_is_reported_not_fatal(self):
        client = Mock()
        client.enrolled = Mock(side_effect=BotSeshError('network_unavailable'))
        result = await media.inbound(client, 'dm', [{'number': 1, 'attachments': [{'id': 'f', 'name': 'a.png', 'size': 5}]}])
        self.assertEqual(result['media_urls'], [])
        self.assertIn('download failed', result['files'][0]['reason'])

    async def test_no_attachments_means_no_media(self):
        self.assertIsNone(await media.inbound(Mock(), 'dm', [{'number': 1, 'text': 'hi'}]))

    async def test_real_client_wrapper_carries_download_and_upload_results(self):
        # Through Client.enrolled (its post-op bookkeeping reads dict results), not a mock of it.
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Store(Path(tmp) / 'store'))
            client.store.update(lambda d: d['rooms'].update({'dm': {'active': True, 'room_id': 'dm', 'room_type': 'dm', 'transport': 'oauth', 'url': 'https://botsesh.com/b/dm'}}))
            def call(_self, binding, op, **args):
                if op == 'download_file':
                    return {'data': PNG, 'sha256': hashlib.sha256(PNG).hexdigest()}
                return {'client_id': args['client_id'], 'number': 12, 'attachments': [], 'read_effect': 'none'}
            with patch('hermes_botsesh_test.oauth.OAuthClient.call', call), patch.object(media, '_cache', side_effect=lambda d, n, m: '/tmp/x.png'):
                got = await media.inbound(client, 'dm', [{'number': 3, 'attachments': [{'id': '6f1c1a8e-0000-4000-8000-000000000001', 'name': 'a.png', 'size': len(PNG)}]}])
                sent = media.upload(client, 'dm', [('a.png', PNG)], scope='s')
        self.assertEqual(got['media_types'], ['image/png'])
        self.assertEqual(sent['number'], 12)


class Outbound(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_read_local_refuses_symlinks_relative_paths_and_oversize(self):
        real = self.root / 'a.png'
        real.write_bytes(PNG)
        link = self.root / 'link.png'
        link.symlink_to(real)
        self.assertEqual(media.read_local([str(real)]), [('a.png', PNG)])
        for bad in (str(link), 'a.png', str(self.root / 'missing.png')):
            with self.assertRaises(BotSeshError):
                media.read_local([bad])
        big = self.root / 'big.bin'
        big.write_bytes(b'\0' * (media.MAX_MESSAGE_BYTES + 1))
        with self.assertRaises(BotSeshError):
            media.read_local([str(big)])

    def test_one_stable_identity_per_exact_intent(self):
        client = Client(Store(self.root / 'store'))
        files = [('a.png', PNG)]
        first = media.upload_identity(client, 'dm', files, text='', voice=False, reply_to=3, scope='turn-1')
        self.assertEqual(first, media.upload_identity(client, 'dm', files, text='', voice=False, reply_to=3, scope='turn-1'))
        self.assertNotEqual(first, media.upload_identity(client, 'dm', [('a.png', PNG + b'x')], text='', voice=False, reply_to=3, scope='turn-1'))
        self.assertNotEqual(first, media.upload_identity(client, 'dm', files, text='', voice=True, reply_to=3, scope='turn-1'))
        self.assertNotEqual(first, media.upload_identity(client, 'dm', files, text='', voice=False, reply_to=3, scope='turn-2'))

    async def adapter(self, room_type):
        from hermes_botsesh_test.adapter import BotSeshAdapter
        from gateway.config import PlatformConfig
        from gateway.platform_registry import PlatformEntry, platform_registry
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        client = Client(Store(self.root / ('store-' + room_type)))
        client.store.update(lambda d: d['rooms'].update({'r': {'active': True, 'room_id': 'r', 'room_type': room_type, 'transport': 'oauth', 'url': 'https://botsesh.com/b/r'}}))
        calls = []
        def enrolled(room_id, op, **args):
            calls.append((op, args))
            return {'number': 40 + len(calls), 'attachments': []}
        client.enrolled = Mock(side_effect=enrolled)
        with patch('hermes_botsesh_test.adapter.Client', return_value=client):
            adapter = BotSeshAdapter(PlatformConfig())
        return adapter, calls

    async def test_dm_sends_exact_files_threaded_to_the_turn_and_voice_only_for_ogg(self):
        adapter, calls = await self.adapter('dm')
        pdf, img, ogg, mp3 = (self.root / n for n in ('report.pdf', 'chart.png', 'reply.ogg', 'song.mp3'))
        pdf.write_bytes(PDF); img.write_bytes(PNG); ogg.write_bytes(OGG); mp3.write_bytes(b'ID3' + b'\0' * 20)
        adapter._current['r'] = {'receipt_id': 'rc', 'target': 7, 'client_id': 'turn-uuid', 'delivered': False}
        self.assertTrue((await adapter.send_document('r', str(pdf))).success)
        self.assertTrue((await adapter.send_image_file('r', str(img), caption='Chart')).success)
        self.assertTrue((await adapter.send_voice('r', str(ogg))).success)
        self.assertTrue((await adapter.send_voice('r', str(mp3))).success)
        ops = [c[1] for c in calls if c[0] == 'upload_files']
        self.assertEqual([o['files'] for o in ops], [[('report.pdf', PDF)], [('chart.png', PNG)], [('reply.ogg', OGG)], [('song.mp3', b'ID3' + b'\0' * 20)]])
        self.assertEqual([o['voice'] for o in ops], [False, False, True, False])
        self.assertEqual({o['reply_to'] for o in ops}, {7})
        self.assertEqual(ops[1]['text'], 'Chart')
        self.assertEqual(adapter._current['r']['media_sent'], [41, 42, 43, 44])

    async def test_gallery_is_one_message_of_up_to_five(self):
        adapter, calls = await self.adapter('dm')
        paths = []
        for i in range(6):
            p = self.root / ('p%d.png' % i)
            p.write_bytes(PNG + bytes([i]))
            paths.append(('file://' + str(p), ''))
        self.assertTrue((await adapter.send_multiple_images('r', paths)).success)
        self.assertEqual([len(c[1]['files']) for c in calls if c[0] == 'upload_files'], [5, 1])

    async def test_group_rooms_stay_text_only(self):
        adapter, calls = await self.adapter('group')
        img = self.root / 'a.png'
        img.write_bytes(PNG)
        with patch('gateway.platforms.base.BasePlatformAdapter.send_image_file', return_value=Mock(success=True)) as core:
            await adapter.send_image_file('r', str(img))
        core.assert_called_once()
        self.assertEqual([c for c in calls if c[0] == 'upload_files'], [])

    async def test_cron_files_go_only_to_the_owner_dm(self):
        from hermes_botsesh_test import adapter as adapter_mod
        img = self.root / 'daily.png'
        img.write_bytes(PNG)
        for room_type, expect_upload in (('group', False), ('dm', True)):
            client = Client(Store(self.root / ('cron-' + room_type)))
            client.store.update(lambda d: d['rooms'].update({'r': {'active': True, 'room_id': 'r', 'room_type': room_type, 'transport': 'oauth', 'url': 'https://botsesh.com/b/r'}}))
            client.enrolled = Mock(return_value={'number': 9, 'attachments': []})
            with patch.object(adapter_mod, 'Client', return_value=client), \
                 patch('gateway.platforms.base.BasePlatformAdapter.filter_media_delivery_paths', side_effect=lambda files, *a, **k: list(files)):
                result = await adapter_mod.standalone_send({}, 'r', 'Daily report', media_files=[(str(img), False)])
            uploads = [c for c in client.enrolled.call_args_list if c.args[1] == 'upload_files']
            if expect_upload:
                self.assertEqual(result, {'success': True, 'message_id': '9'})
                self.assertEqual(uploads[0].kwargs['files'], [('daily.png', PNG)])
            else:
                self.assertIn('owner DM', result['error'])
                self.assertEqual(client.enrolled.call_count, 0)


if __name__ == '__main__':
    unittest.main()
