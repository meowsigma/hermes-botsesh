"""Unsupported catalog picker extensions must not bypass native guard policy."""
from test_dm_controls import harness


def test_catalog_has_no_unguarded_model_picker_override():
    from hermes_botsesh_test.adapter import BotSeshAdapter
    from hermes_botsesh_test.public_capabilities import capabilities
    assert not capabilities()['guarded_model_picker']
    assert not hasattr(harness(), 'send_model_picker')
    assert not callable(getattr(BotSeshAdapter, 'send_model_picker', None))


def test_catalog_uses_native_slash_confirmation_fallback():
    from hermes_botsesh_test.adapter import BotSeshAdapter
    from gateway.platforms.base import BasePlatformAdapter
    from hermes_botsesh_test.public_capabilities import capabilities
    assert not capabilities()['slash_confirmation_cards']
    assert BotSeshAdapter.send_slash_confirm is BasePlatformAdapter.send_slash_confirm
