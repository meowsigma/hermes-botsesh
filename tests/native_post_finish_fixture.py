"""Local native read/tool-post/final replay. NO model or production calls."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock, patch

ROOT = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location('native_fixture_plugin', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from native_fixture_plugin import oauth, tools
from native_fixture_plugin.client import Client, BotSeshError
from native_fixture_plugin.storage import Store
from native_fixture_plugin.adapter import BotSeshAdapter, processed_turn, ended_turn
from gateway.config import PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry
from gateway.platforms.event import ProcessingOutcome
from gateway.session_context import set_session_vars, clear_session_vars

initial = json.loads(sys.stdin.readline())
def rpc(self, binding, name, args=None, **kwargs):
    print(json.dumps({'kind': 'rpc', 'name': name, 'args': args or {}}), flush=True)
    response = json.loads(sys.stdin.readline())
    if 'error' in response:
        raise BotSeshError(response['error'], response.get('status'))
    return response['result']
oauth.OAuthClient.rpc = rpc

async def run():
    with tempfile.TemporaryDirectory(prefix='native-post-finish-') as directory:
        os.environ['HERMES_HOME'] = directory
        store = Store(Path(directory) / 'plugin-data/botsesh')
        room = initial['room_id']
        store.update(lambda d: d['rooms'].update({room: {
            'room_id': room, 'url': 'https://botsesh.com/b/' + room,
            'participant_id': initial['participant_id'], 'transport': 'oauth',
            'auth_version': 2, 'active': True, 'name': 'LOCAL FIXTURE'}}))
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=BotSeshAdapter, check_fn=lambda: True))
        adapter = BotSeshAdapter(PlatformConfig())
        adapter.client = Client(store)
        adapter.set_authorization_check(lambda user, kind, chat: user == initial['human_id'] and chat == room)
        admitted = []
        async def admit(event):
            event._gateway_accepted = True
            admitted.append(event)
        adapter.handle_message = admit
        await adapter._check_room(room)
        assert len(admitted) == 1
        event = admitted[0]
        assert event.message_id == str(initial['source_number'])
        await adapter.on_processing_start(event)
        await adapter.send_typing(room)
        tokens = set_session_vars(platform='botsesh', chat_id=room, message_id=event.message_id)
        ctx = Mock()
        ctx.session_surface.return_value = 'botsesh'
        with patch.object(tools, 'Client', return_value=adapter.client):
            try:
                frozen = tools.read({'room_id': room}, ctx)
                assert frozen['read_effect'] == 'none'
                args = {'room_id': room, 'text': 'Hello, plant-swap crew!',
                        'receipt_id': frozen['receipt_id'], 'processed': True}
                if initial['lose_response']:
                    try:
                        tools.post(args, ctx)
                    except BotSeshError as error:
                        assert error.code == 'connection_failed_retry_same_intent'
                    else:
                        raise AssertionError('expected lost response')
                    # Re-open the private registry before resolving the saved UUID.
                    adapter.client = Client(Store(store.root))
                else:
                    tools.post(args, ctx)
                assert 'reply_to' not in args  # Authored tool arguments remain unchanged.
            finally:
                clear_session_vars(tokens)
        processed_turn(platform='botsesh', user_message=adapter._current[room]['text'], session_id='local-scripted', turn_id='one')
        ended_turn(platform='botsesh', session_id='local-scripted', turn_id='one', completed=True, failed=False, interrupted=False)
        final, _ = await adapter.send_final_ledgered(event, 'local-scripted', args['text'], {'notify': True}, reply_to=event.message_id)
        assert final.success, final.error
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        await adapter._check_room(room)
        assert len(admitted) == 1, 'no new native turn may replay inference'
        state = store.room(room)
        assert state['processing_receipts'][event.message_id]['state'] == 'replied'
        assert state['health']['backlog'] == 0, 'server native delivery remains stuck after confirmed tool reply'
        assert room not in adapter._current
        assert 'pending_final' not in state
        posts = list(state['oauth_posts'].values())
        assert len(posts) == 1 and posts[0]['complete'] is True
        assert 'reply_to' not in posts[0]['exact'], 'authored exact operation must not be rewritten'
        print(json.dumps({'kind': 'done', 'state': 'replied', 'backlog': state['health']['backlog'],
                          'client_id': posts[0]['exact']['client_id'], 'inference_calls': 0}), flush=True)
        platform_registry.unregister('botsesh')

asyncio.run(run())
