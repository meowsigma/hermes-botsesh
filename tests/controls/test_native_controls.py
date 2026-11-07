from test_dm_controls import async_test, harness


def approval_prompt(actions=None, metadata=None, command='printf safe', smart_denied=False):
    from gateway.platforms.base import ExecApprovalPrompt
    return ExecApprovalPrompt(chat_id='dm', session_key='session', text='NOT THE EXACT PREVIEW',
        actions=actions or [('Once', 'once', 'primary'), ('Session', 'session', ''),
                            ('Always', 'always', ''), ('Deny', 'deny', 'danger')],
        command=command, description='Requires consent', smart_denied=smart_denied, metadata=metadata)


@async_test
async def test_approval_resolves_only_exact_request_and_respects_native_actions():
    from tools import approval
    from tools.approval_gateway_wait import _ApprovalEntry
    a = harness()
    first = _ApprovalEntry({'request_id': 'old', 'command': 'printf old'})
    target = _ApprovalEntry({'request_id': 'target', 'command': 'printf safe'})
    approval._gateway_queues['session'] = [first, target]
    try:
        result = await a._send_exec_approval_prompt(approval_prompt(metadata={'request_id': 'target'}))
        assert result.success
        wire = a.calls[-1][2]
        assert wire['preview'] == 'printf safe'
        assert [b['label'] for b in wire['buttons']] == ['Approve', 'Approve for session', 'Always approve', 'Deny']
        await a._control_receive('dm', a.choice(wire))
        assert target.result == 'once'
        assert first.result is None
        assert approval.list_gateway_approvals('session') == [dict(first.data)]
        await a._control_receive('dm', a.choice(wire))
        assert first.result is None
        approval._gateway_queues['session'] = [target]
        target.data['request_id'] = 'smart'
        assert (await a._send_exec_approval_prompt(approval_prompt(smart_denied=True))).success
        assert [b['label'] for b in a.calls[-1][2]['buttons']] == ['Approve', 'Deny']
    finally:
        approval._gateway_queues.pop('session', None)


@async_test
async def test_approval_missing_changed_ambiguous_and_oversized_fail_closed():
    from tools import approval
    from tools.approval_gateway_wait import _ApprovalEntry
    a = harness()
    try:
        assert not (await a._send_exec_approval_prompt(approval_prompt())).success
        old = _ApprovalEntry({'request_id': 'old', 'command': 'printf safe'})
        approval._gateway_queues['session'] = [old]
        assert (await a._send_exec_approval_prompt(approval_prompt())).success
        message = a.choice()
        newer = _ApprovalEntry({'request_id': 'newer', 'command': 'printf safe'})
        approval._gateway_queues['session'] = [newer]
        await a._control_receive('dm', message)
        assert newer.result is None
        assert any('expired' in text for _, text in a.notices)
        approval._gateway_queues['session'] = [old, newer]
        assert not (await a._send_exec_approval_prompt(approval_prompt())).success
        approval._gateway_queues['session'] = [_ApprovalEntry({'request_id': 'huge', 'command': '😀' * 2001})]
        before = len(a.calls)
        assert not (await a._send_exec_approval_prompt(approval_prompt(command='😀' * 2001))).success
        assert len(a.calls) == before
    finally:
        approval._gateway_queues.pop('session', None)


@async_test
async def test_slash_confirm_native_resolution_and_superseded_request():
    from tools import slash_confirm
    a = harness()
    seen = []
    async def handler(choice):
        seen.append(choice)
        return 'Reload completed.'
    try:
        slash_confirm.register('session', 'confirm', 'reload-mcp', handler)
        assert (await a.send_slash_confirm('dm', 'Reload?', 'Cache will reset.', 'session', 'confirm')).success
        wire = a.calls[-1][2]
        await a._control_receive('dm', a.choice(wire))
        assert seen == ['once'] and a.notices[-1][1] == 'Reload completed.'
        slash_confirm.register('session', 'old', 'reload-mcp', handler)
        await a.send_slash_confirm('dm', 'Reload?', 'Cache will reset.', 'session', 'old')
        message = a.choice()
        slash_confirm.register('session', 'new', 'reload-mcp', handler)
        await a._control_receive('dm', message)
        assert seen == ['once']
        assert slash_confirm.get_pending('session')['confirm_id'] == 'new'
    finally:
        slash_confirm.clear('session')
