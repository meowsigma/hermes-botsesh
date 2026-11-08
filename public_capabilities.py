"""Catalog capability detection, using only documented Hermes plugin surfaces.

Absence is not permission to reach into core. In particular, runtime status is
not a complete live-work probe (it excludes async delegations), and a picker
callback is not a guarded model switch. Keep those extensions off until a
supported contract exists; native text commands continue to be owned by Hermes.
"""
from gateway.platforms import base


def capabilities():
    prompt = getattr(base, 'ExecApprovalPrompt', None)
    return {
        'request_bound_approval': 'request_id' in getattr(prompt, '__dataclass_fields__', {}),
        # No documented ctx/base API provides these contracts on supported core.
        'live_command_discovery': False,
        'guarded_model_picker': False,
        'slash_confirmation_cards': False,
        'safe_idle_restart': False,
    }
