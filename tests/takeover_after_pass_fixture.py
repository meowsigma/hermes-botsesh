"""Real adapter/OAuth/tools over parent-owned exact-server RPC; no inference."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import weakref
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location('pass_takeover_plugin', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from pass_takeover_plugin import adapter as mod, oauth, tools
from pass_takeover_plugin.client import Client, BotSeshError
from pass_takeover_plugin.storage import Store
from pass_takeover_plugin.room_pass import pass_frozen
from gateway.config import PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry
from gateway.session_context import set_session_vars, clear_session_vars

initial = json.loads(sys.stdin.readline())
def exchange(message):
    print(json.dumps(message), flush=True)
    response = json.loads(sys.stdin.readline())
    if 'error' in response:
        raise BotSeshError(response['error'], response.get('status'))
    return response['result']
def rpc(self, binding, name, args=None, **kwargs):
    return exchange({'kind': 'rpc', 'name': name, 'args': args or {}})
oauth.OAuthClient.rpc = rpc

async def run():
    assert shutil.which('node') is None and shutil.which('nodejs') is None
    with tempfile.TemporaryDirectory(prefix='pass-takeover-') as directory:
        os.environ['HERMES_HOME'] = directory
        room, target = initial['room_id'], initial['source_number']
        store = Store(Path(directory) / 'plugin-data/botsesh')
        store.update(lambda d: d['rooms'].update({room: {'room_id': room, 'url': 'https://botsesh.com/b/' + room,
            'origin': 'https://botsesh.com', 'participant_id': initial['participant_id'], 'oauth_id': 'fixture',
            'transport': 'oauth', 'active': True, 'name': 'LOCAL'}}))
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=mod.BotSeshAdapter, check_fn=lambda: True))
        with patch.object(mod, '_ADAPTERS', weakref.WeakSet()):
            adapter = mod.BotSeshAdapter(PlatformConfig())
            adapter.client = Client(store)
            adapter.set_authorization_check(lambda user, kind, rid: user == initial['human_id'] and rid == room)
            # Script only execution; no provider is instantiated. Keep the actual
            # adapter context delivery, pass/claim/post and terminal settlement.
            adapter._admit_event = AsyncMock(return_value=True)
            await adapter._check_room(room)
            ambient = adapter._admit_event.call_args.args[0]
            assert ambient.source.message_id == str(target)
            current = adapter._current[room]
            old_receipt = current['receipt_id']
            pass_frozen(adapter.client, room, old_receipt, True, target=target)
            await adapter._finish_current(room, current, 'interrupted')
            passed = store.room(room)['processing_receipts'][str(target)]
            assert passed['state'] == 'passed' and passed['explicit_pass']['result']
            await adapter._check_room(room)
            assert adapter._admit_event.await_count == 1, 'duplicate ambient turn was admitted'
            exchange({'kind': 'primary_failure'})
            await adapter._check_room(room)
            assert adapter._admit_event.await_count == 2, 'prior ambient pass suppressed the canonical takeover'
            takeover = adapter._admit_event.call_args.args[0]
            current = adapter._current[room]
            row = store.room(room)['processing_receipts'][str(target)]
            assert current['batch_targets'] == [target], 'takeover source cannot be omitted from the frozen pending inputs'
            assert row['receipt_id'] != old_receipt
            assert 'explicit_pass' not in row, 'old pass must not terminate a new work item'
            assert takeover.message_id != ambient.message_id, 'Hermes ingress dedupes by event message_id'
            assert takeover.source.message_id == str(target), 'tool scope must retain the exact source number'
            assert store.room(room)['work_receipts']['message:' + str(target)] == passed
            ctx = Mock(); tools.register_tools(ctx)
            registered = {c.kwargs['name']: c.kwargs for c in ctx.register_tool.call_args_list}
            tokens = set_session_vars(platform='botsesh', chat_id=room, message_id=str(target))
            try:
                denied = json.loads(registered['botsesh_takeover_claim']['handler']({'reason': 'I can help', 'processed': False}))
                assert denied['ok'] is False
                wrong = set_session_vars(platform='botsesh', chat_id='foreign-room', message_id=str(target))
                try:
                    denied = json.loads(registered['botsesh_takeover_claim']['handler']({'reason': 'I can help', 'processed': True}))
                    assert denied['ok'] is False
                finally:
                    clear_session_vars(wrong)
                # A missing/foreign receipt cannot authorize a claim. Keep the
                # ambient pass and its retained exact receipt untouched.
                try:
                    adapter.client.enrolled(room, 'takeover_claim', event_id=row['takeover_offer']['event_id'], receipt_id='foreign-receipt', reason='I can help', processed=True)
                    raise AssertionError('foreign receipt accepted')
                except BotSeshError as error:
                    assert error.code == 'processed_receipt_required'
                claimed = json.loads(registered['botsesh_takeover_claim']['handler']({'reason': 'I can help with a text fallback', 'processed': True}))
                assert claimed['ok'] is True and claimed['result']['granted'] is True, claimed
                assert json.loads(registered['botsesh_takeover_claim']['handler']({'reason': 'I can help with a text fallback', 'processed': True})) == claimed
            finally:
                clear_session_vars(tokens)
            current['model_processed'] = True
            result = await adapter.send(room, 'LOCAL scripted backup text fallback.', metadata={'notify': True})
            assert result.success, result.error
            await adapter._finish_current(room, current, 'empty_response')
            assert store.room(room)['processing_receipts'][str(target)]['takeover_settled'] is True
            assert store.room(room)['work_receipts']['message:' + str(target)] == passed
            await adapter._check_room(room)
            assert adapter._admit_event.await_count == 2
            # Restart retains dedupe, not just the old process's in-memory set.
            restarted = mod.BotSeshAdapter(PlatformConfig()); restarted.client = adapter.client
            restarted._admit_event = AsyncMock(return_value=True)
            await restarted._check_room(room)
            restarted._admit_event.assert_not_awaited()
            print(json.dumps({'kind': 'done', 'claim_granted': True, 'ambient_duplicates_deduped': True,
                'native_events': 2, 'node_available': False, 'model_calls': 0,
                'ambient_pass_preserved': True, 'distinct_native_delivery_id': True}), flush=True)
        platform_registry.unregister('botsesh')

asyncio.run(run())
