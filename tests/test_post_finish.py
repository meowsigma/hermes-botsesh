"""Focused native association boundaries; no network or inference."""
import tempfile
import unittest
import uuid
from pathlib import Path

from test_plugin import Client, Store
from hermes_botsesh_test.oauth import OAuthClient


class PostFinishTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.client = Client(Store(Path(tmp.name) / 'private'))
        self.api = OAuthClient(self.client.store)
        self.exact = {'text': 'Exact café 🐚\n', 'client_id': str(uuid.uuid4()),
                      'receipt_id': 'current', 'processed': True}
        self.row = {'target': 0, 'receipt_id': 'current', 'state': 'started'}

    def seed(self, rows, numbers=(0,)):
        self.client.store.update(lambda d: d['rooms'].update(a={
            'active': True, 'processing_receipts': rows, 'oauth_receipts': {'current': {'numbers': list(numbers)}}}))

    def test_association_requires_one_active_exact_receipt_and_no_explicit_target(self):
        cases = [
            ('current', {'0': self.row}, (0,), {}, 0),
            ('manual', {}, (0,), {}, None),
            ('foreign receipt', {'0': {**self.row, 'receipt_id': 'other'}}, (0,), {}, None),
            ('unread target', {'0': self.row}, (2,), {}, None),
            ('settled', {'0': {**self.row, 'state': 'replied'}}, (0,), {}, None),
            ('failed', {'0': {**self.row, 'state': 'failed'}}, (0,), {}, None),
            ('ambiguous', {'0': self.row, '2': {**self.row, 'target': 2}}, (0, 2), {}, None),
            ('explicit zero', {'0': self.row}, (0,), {'reply_to': 0}, None),
            ('explicit other', {'0': self.row}, (0, 2), {'reply_to': 2}, None),
            ('explicit null', {'0': self.row}, (0,), {'reply_to': None}, None),
            ('unprocessed', {'0': self.row}, (0,), {'processed': False}, None),
        ]
        for name, rows, numbers, options, expected in cases:
            with self.subTest(name=name):
                self.seed(rows, numbers)
                exact = {**self.exact, **options}
                saved = self.api._journal('a', 'oauth_posts', exact['client_id'], exact)
                self.assertEqual(saved.get('native_reply_to'), expected)
                self.assertEqual(saved['exact'], exact, 'authored fields and UUID stay exact')

    def test_existing_uncertain_journal_never_gains_or_changes_association(self):
        self.seed({'0': self.row})
        saved = self.api._journal('a', 'oauth_posts', self.exact['client_id'], self.exact)
        self.assertEqual(saved['native_reply_to'], 0)
        self.seed({'2': {**self.row, 'target': 2}}, (2,))
        self.client.store.update(lambda d: d['rooms']['a'].update(oauth_posts={self.exact['client_id']: saved}))
        self.assertEqual(self.api._journal('a', 'oauth_posts', self.exact['client_id'], self.exact), saved)
        # Old unassociated UUIDs retain their original wire shape, not a retrofit.
        old = {'exact': self.exact}
        self.client.store.update(lambda d: d['rooms']['a'].update(oauth_posts={self.exact['client_id']: old}))
        self.assertEqual(self.api._journal('a', 'oauth_posts', self.exact['client_id'], self.exact), old)

    def test_structured_declarations_are_not_rewritten(self):
        self.seed({'0': self.row})
        exact = {'output': {'reply': self.exact['text'], 'draft_ready': {'title': 'Exact title', 'blockers': ['Exact blocker']}},
                 'receipt_id': 'current', 'processed': True, 'client_id': self.exact['client_id']}
        saved = self.api._journal('a', 'oauth_turns', exact['client_id'], exact)
        self.assertEqual(saved, {'exact': exact})
