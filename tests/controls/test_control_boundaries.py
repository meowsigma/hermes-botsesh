import asyncio
from copy import deepcopy
import inspect
from uuid import UUID

import pytest

from test_dm_controls import async_test, harness


async def picker(a, callback=None):
    async def selected(chat_id, value):
        if callback is not None:
            callback.append((chat_id, value))
        return 'Done.'
    return await a.send_choice_picker('dm', 'Setting', [{'value': 'exact/value', 'label': 'Pick'}], 'session', selected)


@async_test
async def test_edit_pending_uses_callback_update_selected_is_immutable_and_regular_delegates():
    a = harness()
    result = await picker(a)
    wire = a.calls[-1][2]
    edit = await a.edit_message('dm', result.message_id, 'Updated text', finalize=True)
    assert edit.success
    assert a.calls[-1][1] == 'update'
    update = a.calls[-1][2]
    assert update['callbackId'] == wire['callbackId'] and update['text'] == 'Updated text'
    assert set(update) == {'version', 'clientId', 'callbackId', 'text'}
    await a._control_receive('dm', a.choice(wire))
    assert not (await a.edit_message('dm', result.message_id, 'Cannot overwrite chosen card')).success
    assert not a.edits
    assert (await a.edit_message('dm', 'ordinary', 'Normal edit', finalize=True)).success
    assert a.edits == [('dm', 'ordinary', 'Normal edit', True)]


@async_test
async def test_uncertain_send_and_edit_reuse_exact_uuids_without_hidden_retry_loops():
    a = harness()
    a.fail_once = True
    async def selected(*args):
        return 'Done.'
    args = ('dm', 'Setting', [{'value': 'v', 'label': 'V'}], 'session', selected)
    result = await a.send_choice_picker(*args)
    assert not result.success and len(a.calls) == 1
    original = deepcopy(a.calls[-1][2])
    result = await a.send_choice_picker(*args)
    assert result.success and a.calls[-1][2] == original and len(a._controls()) == 1
    a.fail_once = True
    result = await a.edit_message('dm', result.message_id, 'New text')
    assert not result.success
    update = deepcopy(a.calls[-1][2])
    assert (await a._control_retry(result.raw_response['control_client_id'])).success
    assert a.calls[-1][2] == update
    for _, _, payload in a.calls:
        assert UUID(payload['clientId']).version == 4


@pytest.mark.parametrize('mutate', [
    lambda a, m: m.update(senderParticipantId='foreign'),
    lambda a, m: m['authorParticipant'].update(verified=False),
    lambda a, m: m['authorParticipant'].update(id='foreign'),
    lambda a, m: m['dmControl'].update(agentMembershipId='other'),
    lambda a, m: m['dmControl'].update(version=2),
    lambda a, m: m['dmControl'].update(version=True),
    lambda a, m: m['dmControl'].update(sourceNumber=999),
    lambda a, m: m['dmControl'].update(sourceNumber=True),
    lambda a, m: m['dmControl'].update(optionId='made-up'),
    lambda a, m: m['dmControl'].update(optionId={}),
    lambda a, m: m['dmControl'].update(callbackId=[]),
    lambda a, m: setattr(a, 'authorized', None),
    lambda a, m: setattr(a, 'authorized', False),
])
@async_test
async def test_untrusted_choice_is_consumed_without_claiming_or_callback(mutate):
    a, seen = harness(), []
    await picker(a, seen)
    valid = a.choice()
    bad = deepcopy(valid)
    mutate(a, bad)
    assert await a._control_receive('dm', bad) is True
    assert seen == []
    assert not next(iter(a._controls().values())).claimed
    a.authorized = True
    await a._control_receive('dm', valid)
    assert seen == [('dm', 'exact/value')]


@async_test
async def test_expiry_generation_session_membership_restart_and_capacity(monkeypatch):
    import dm_controls
    a, seen = harness(), []
    await picker(a, seen)
    choice = a.choice()
    a.ctx['generation'] = 2
    await a._control_receive('dm', choice)
    assert not seen
    a.ctx['generation'] = 1
    a.ctx['session_key'] = 'replacement'
    await a._control_receive('dm', choice)
    assert not seen
    a.ctx['session_key'] = 'session'
    now = dm_controls.time.time()
    monkeypatch.setattr(dm_controls.time, 'time', lambda: now + 301)
    await a._control_receive('dm', choice)
    assert not seen and 'expired' in a.notices[-1][1]
    await a._control_sweep()
    assert not a._controls() and not a._dm_operations
    b = harness()
    await b._control_receive('dm', choice)
    assert 'expired' in b.notices[-1][1] and not b.calls
    b.CONTROL_MAX_CARDS = 2
    assert (await picker(b)).success
    assert (await picker(b)).success
    assert not (await picker(b)).success
    assert len(b._controls()) == 2
    b.ctx['generation'] += 1
    await b._control_invalidate('dm')
    assert not b._controls() and all(card[1] in ('send', 'retire') for card in b.calls)
    assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]


@async_test
async def test_native_pending_disappears_sweep_retires_without_new_tasks():
    from tools import clarify_gateway as clarify
    a = harness()
    clarify.register('retire', 'session', 'Q', ['A'])
    try:
        await a.send_clarify('dm', 'Q', ['A'], 'retire', 'session')
        clarify.clear_session('session')
        await a._control_sweep()
        assert a.calls[-1][1] == 'retire' and not a._controls()
    finally:
        clarify.clear_session('session')


def test_hook_parameter_names_match_native_sources():
    from dm_controls import DmControlsMixin
    from gateway.platforms.base import BasePlatformAdapter
    # Telegram's two picker hooks are adapter APIs, not declared on Base.
    import ast
    source = (inspect.getfile(BasePlatformAdapter))
    from pathlib import Path
    tree = ast.parse(Path(source).read_text())
    telegram = ast.parse((Path(source).parents[2] / 'plugins/platforms/telegram/adapter.py').read_text())
    for name in ('send_clarify', 'edit_message', '_send_exec_approval_prompt', 'send_choice_picker'):
        native = next(n for n in ast.walk(telegram if 'picker' in name else tree)
                      if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        native_args = [a.arg for a in native.args.args] + [a.arg for a in native.args.kwonlyargs]
        ours = list(inspect.signature(getattr(DmControlsMixin, name)).parameters)
        assert ours == native_args
