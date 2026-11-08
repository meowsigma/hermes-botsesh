"""Owner-DM regressions: real core authorization; no network or inference."""
import os
import time
from unittest.mock import patch
from test_commands import NativeCommandTests


class OwnerConsentTests(NativeCommandTests):
    async def test_consented_owner_admitted_without_global_allowlist(self):
        os.environ['GATEWAY_ALLOWED_USERS'] = ''
        self.client.store.update(lambda d: d['rooms']['native-dm'].update(
            room_id='native-dm', oauth_id='grant', oauth_root='source'))
        original = self.client.enrolled
        live = dict(room_id='native-dm', room_type='dm', human_participant_id='human-owner',
                    membership_id='agent-membership', participant_id='agent-participant',
                    human_owns_grant=True, access_state='authorized')
        def enrolled(room, op, **kwargs):
            return dict(live) if op == 'dm_context' else original(room, op, **kwargs)
        self.client.enrolled = enrolled
        await self.adapter._refresh_dm_controls('native-dm')
        self.assertIs(self.adapter._is_sender_authorized('human-owner', 'dm', 'native-dm'), True)
        self.assertTrue(self.runner._is_user_authorized(self.source))
        self.assertTrue(self.runner._is_user_authorized_for_source(self.source, allow_adapter_delegation=False),
                        'native envelope rechecks without adapter-policy delegation')
        self.assertIs(self.adapter._is_sender_authorized('foreign', 'dm', 'native-dm'), False)
        self.adapter._dm_contexts['native-dm']['until'] = 0
        self.assertIs(self.adapter._is_sender_authorized('human-owner', 'dm', 'native-dm'), False)
        live['human_owns_grant'] = False
        await self.adapter._refresh_dm_controls('native-dm')
        self.assertIs(self.adapter._is_sender_authorized('human-owner', 'dm', 'native-dm'), False)
        live['human_owns_grant'] = True
        live['access_state'] = 'paused'
        await self.adapter._refresh_dm_controls('native-dm')
        self.assertIs(self.adapter._is_sender_authorized('human-owner', 'dm', 'native-dm'), False)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromName('test_consented_owner_admitted_without_global_allowlist', OwnerConsentTests)
