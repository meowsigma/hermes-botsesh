"""Local-only contract tests; Hermes source is imported read-only."""
import asyncio
from copy import deepcopy
import sys
from functools import wraps
from pathlib import Path


def async_test(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return run

import pytest

import gateway
CORE = Path(gateway.__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(Path(__file__).parent))


def test_mixin_is_implemented():
    import importlib.util
    assert importlib.util.find_spec('dm_controls') is not None, 'DM controls module not implemented'


class HarnessBase:
    def __init__(self):
        self.ctx = dict(human_id='human', membership_id='member', generation=1, session_key='session')
        self.calls, self.notices, self.edits = [], [], []
        self.authorized = True
        self.next_number = 10
        self.fail_once = False

    def _control_context(self, chat_id):
        return self.ctx.copy() if chat_id == 'dm' else None

    def _is_sender_authorized(self, user_id, chat_type=None, chat_id=None):
        assert (user_id, chat_type, chat_id) == ('human', 'dm', 'dm')
        return self.authorized

    async def _control_call(self, chat_id, action, payload):
        self.calls.append((chat_id, action, deepcopy(payload)))
        if self.fail_once:
            self.fail_once = False
            raise TimeoutError('uncertain transport')
        if action == 'send':
            self.next_number += 1
            return {'message': {'number': self.next_number, 'id': 'db-id-not-room-number',
                    'dmControl': {'kind': 'interactive', 'version': 1, 'status': 'pending',
                                  'callbackId': payload['callbackId'], 'agentMembershipId': 'member'}},
                    'duplicate': False}
        return {'message': {'number': 11}, 'duplicate': False}

    async def _control_notice(self, chat_id, text):
        from gateway.platforms.base import SendResult
        self.notices.append((chat_id, text))
        return SendResult(success=True, message_id='notice')

    async def _control_edit(self, chat_id, message_id, content, *, finalize=False):
        from gateway.platforms.base import SendResult
        self.edits.append((chat_id, message_id, content, finalize))
        return SendResult(success=True, message_id=message_id)

    def choice(self, payload=None, **overrides):
        payload = payload or self.calls[-1][2]
        control = dict(version=1, kind='choice', callbackId=payload['callbackId'],
                       optionId=payload['choices'][0]['id'] if payload['choices'] else payload['buttons'][0]['id'],
                       agentMembershipId='member', sourceNumber=self.next_number)
        control.update(overrides)
        return {'senderParticipantId': 'human', 'authorParticipant': {'id': 'human', 'verified': True},
                'dmControl': control, 'number': self.next_number + 1}


def harness():
    from dm_controls import DmControlsMixin
    class Adapter(DmControlsMixin, HarnessBase):
        pass
    return Adapter()


@async_test
async def test_flat_callback_is_claimed_before_await_and_replay_is_not_inference():
    a = harness()
    seen = []
    started, release = asyncio.Event(), asyncio.Event()
    async def selected(chat_id, value):
        seen.append((chat_id, value))
        started.set()
        await release.wait()
        return 'Reasoning updated.'
    result = await a.send_choice_picker('dm', 'Reasoning', [{'value': 'full:value', 'label': 'Full'}],
                                        'session', selected)
    assert result.success and result.message_id == '11'
    message = a.choice()
    task = asyncio.create_task(a._control_receive('dm', message))
    await started.wait()
    assert await a._control_receive('dm', message) is True
    release.set()
    assert await task is True
    assert seen == [('dm', 'full:value')]
    assert any(text == 'Reasoning updated.' for _, text in a.notices)
    assert await a._control_receive('dm', {'text': 'Selected: Full'}) is False


@async_test
async def test_clarify_checks_native_identity_other_and_multi_text_recovery():
    from tools import clarify_gateway as clarify
    a = harness()
    entry = clarify.register('clarify-one', 'session', 'Which?', ['Alpha', 'Beta'])
    try:
        result = await a.send_clarify('dm', 'Which?', ['Alpha', 'Beta'], 'clarify-one', 'session')
        assert result.success
        wire = a.calls[-1][2]
        await a._control_receive('dm', a.choice(wire))
        assert entry.response == 'Alpha'
        assert clarify.get_pending('clarify-one') is None
        assert clarify.wait_for_response('clarify-one', 0.01) == 'Alpha'
        entry2 = clarify.register('clarify-two', 'session', 'Which?', ['Alpha', 'Beta'])
        assert (await a.send_clarify('dm', 'Which?', ['Alpha', 'Beta'], 'clarify-two', 'session')).success
        wire = a.calls[-1][2]
        await a._control_receive('dm', a.choice(wire, optionId=wire['buttons'][0]['id']))
        assert entry2.awaiting_text and not entry2.event.is_set()
        assert clarify.resolve_text_response_for_session('session', 'custom answer')
        assert entry2.response == 'custom answer'
        assert clarify.wait_for_response('clarify-two', 0.01) == 'custom answer'
        multi = clarify.register('multi', 'session', 'Pick several', ['A', 'B'], multi_select=True)
        assert (await a.send_clarify('dm', 'Pick several', ['A', 'B'], 'multi', 'session')).success
        assert multi.awaiting_text
        assert clarify.resolve_text_response_for_session('session', '1,2')
        assert multi.response == '["A", "B"]'
    finally:
        clarify.clear_session('session')


@async_test
async def test_clarify_replaced_same_id_does_not_resolve_replacement():
    from tools import clarify_gateway as clarify
    a = harness()
    clarify.register('reused', 'session', 'Old?', ['Old'])
    try:
        await a.send_clarify('dm', 'Old?', ['Old'], 'reused', 'session')
        message = a.choice()
        replacement = clarify.register('reused', 'session', 'New?', ['New'])
        await a._control_receive('dm', message)
        assert replacement.response is None
        assert any('expired' in text for _, text in a.notices)
    finally:
        clarify.clear_session('session')
