from unittest.mock import patch
import pytest
from test_dm_controls import async_test, harness


@pytest.mark.parametrize('choices', [['Red', 'Blue'], None])
@pytest.mark.parametrize('translation', ['Répondez « skip » pour ignorer.', 'gateway.clarify.skip_hint'])
@async_test
async def test_native_clarify_copy_preserves_exact_pending_identity(choices, translation):
    from tools import clarify_gateway as clarify
    a = harness()
    question = 'Pick a color.\ngateway.clarify.skip_hint'
    entry = clarify.register(clarify_id='copy-test', session_key='session', question=question, choices=choices)
    try:
        with patch('agent.i18n.t', return_value=translation):
            result = await a.send_clarify('dm', question, choices, 'copy-test', 'session')
        assert result.success
        text = a.calls[-1][2]['text'] if choices else a.notices[-1][1]
        assert text.startswith('Pick a color.\n') and 'gateway.' not in text
        assert text.endswith(translation if translation != 'gateway.clarify.skip_hint' else 'Reply "skip" to skip this question.')
        assert entry.question == question and entry.choices == choices
        if choices:
            payload = a.calls[-1][2]
            assert [c['label'] for c in payload['choices']] == choices
            blue = dict(payload, choices=[payload['choices'][1]])
            await a._control_receive('dm', a.choice(blue))
            assert entry.response == 'Blue'
        else:
            assert clarify.resolve_gateway_clarify('copy-test', 'Blue')
            assert entry.response == 'Blue'
    finally:
        clarify.clear_session('session')
