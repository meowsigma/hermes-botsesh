from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_dm_controls import async_test, harness
from test_native_controls import approval_prompt


@async_test
async def test_malformed_send_response_retains_original_uuid_for_exact_retry():
    a = harness()
    original_call = a._control_call
    async def malformed(chat, action, payload):
        response = await original_call(chat, action, payload)
        response['message']['dmControl'] = ['not-a-projection']
        return response
    a._control_call = malformed
    async def selected(*args):
        raise AssertionError('must not dispatch')
    args = ('dm', 'Setting', [{'label': 'A', 'value': 'a'}], 'session', selected)
    result = await a.send_choice_picker(*args)
    assert not result.success
    payload = deepcopy(a.calls[-1][2])
    a._control_call = original_call
    result = await a.send_choice_picker(*args)
    assert result.success and a.calls[-1][2] == payload


@pytest.mark.parametrize('permanent,session,smart,expected', [
    (True, True, False, ['Approve', 'Approve for session', 'Always approve', 'Deny']),
    (False, True, False, ['Approve', 'Approve for session', 'Deny']),
    (True, False, False, ['Approve', 'Deny']),
    (True, True, True, ['Approve', 'Deny']),
])
@async_test
async def test_real_base_template_normalizes_approval_actions(permanent, session, smart, expected):
    from gateway.platforms.base import BasePlatformAdapter
    from tools import approval
    from tools.approval_gateway_wait import _ApprovalEntry
    a = harness()
    labels = {'once': 'Once', 'session': 'Session', 'always': 'Always', 'deny': 'Deny'}
    template = SimpleNamespace(_EA_ACTION_LABELS=labels, _EA_ACTION_STYLES={'once': 'primary', 'deny': 'danger'})
    a._format_exec_approval = lambda *args: 'Core display template'
    a._exec_approval_actions = lambda **flags: BasePlatformAdapter._exec_approval_actions(template, **flags)
    native = _ApprovalEntry({'command': 'printf safe', 'request_id': 'r'})
    approval._gateway_queues['session'] = [native]
    try:
        result = await BasePlatformAdapter.send_exec_approval(a, 'dm', 'printf safe', 'session',
            allow_permanent=permanent, allow_session=session, smart_denied=smart, request_id='r')
        assert result.success
        wire = a.calls[-1][2]
        assert [b['label'] for b in wire['buttons']] == expected
        deny = wire['buttons'][-1]['id']
        await a._control_receive('dm', a.choice(wire, optionId=deny))
        assert native.result == 'deny'
    finally:
        approval._gateway_queues.pop('session', None)


@async_test
async def test_uncertain_update_cannot_retry_after_card_selection():
    a = harness()
    async def selected(*args):
        return 'Updated.'
    result = await a.send_choice_picker('dm', 'Setting', [{'label': 'A', 'value': 'a'}], 'session', selected)
    wire = a.calls[-1][2]
    a.fail_once = True
    uncertain = await a.edit_message('dm', result.message_id, 'Updated title')
    assert not uncertain.success
    await a._control_receive('dm', a.choice(wire))
    before = len(a.calls)
    assert not (await a._control_retry(uncertain.raw_response['control_client_id'])).success
    assert len(a.calls) == before


@async_test
async def test_retire_clarify_and_group_outbound_rejection():
    from tools import clarify_gateway as clarify
    a = harness()
    entry = clarify.register('retire-me', 'session', 'Question?', ['Answer'])
    try:
        result = await a.send_clarify('dm', 'Question?', ['Answer'], 'retire-me', 'session')
        assert result.success
        wire = a.calls[-1][2]
        await a.retire_clarify_card('retire-me', 'Typed answer superseded this card.')
        assert a.calls[-1][1] == 'retire'
        await a._control_receive('dm', a.choice(wire))
        assert entry.response is None
        async def selected(*args):
            raise AssertionError('group callback')
        before = len(a.calls)
        assert not (await a.send_choice_picker('group', 'Title', [{'value': 'a', 'label': 'A'}], 'session', selected)).success
        assert len(a.calls) == before
    finally:
        clarify.clear_session('session')


@async_test
async def test_open_clarify_notice_seam_may_complete_without_result():
    from tools import clarify_gateway as clarify
    a = harness()
    original = a._control_notice
    async def notice(chat_id, text):
        await original(chat_id, text)
    a._control_notice = notice
    clarify.register('open', 'session', 'Type your answer', None)
    try:
        result = await a.send_clarify('dm', 'Type your answer', None, 'open', 'session')
        assert result is not None and result.success
        assert clarify.get_pending('open').awaiting_text
        assert not a.calls
    finally:
        clarify.clear_session('session')
