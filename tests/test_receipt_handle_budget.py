"""Guard accounting is about native terminal turns, not visible contributions.

Scripted no-inference requests mirror the 11i reservation order. A successful
fourth Ari post MUST NOT buy an implicit fifth summary call. Short r1 labels
are per-input and need a scoped ledger key, never a shared global r1 bucket.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/agreement_trial'))
from trial_limits import TrialBudget, SampleStopped, frozen_receipt
from test_receipt_handles import ARI_RECEIPT


def request(receipt, target=0, room='i4PZ6RebQOT3g9oWcREiHS87fLC_qyHc'):
    return {'messages': [{'role': 'user', 'content': json.dumps({
        'receipt_id': receipt, 'room_id': room, 'addressed_message_number': target})}], 'tools': []}


class ReceiptBudgetTests(unittest.TestCase):
    def test_11i_fourth_post_does_not_reset_native_receipt_or_authorize_fifth_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = []
            budget = TrialBudget(temp, ledger, allowance=29)
            for actor, rid in [('ari', ARI_RECEIPT), ('bo', 'f701bf31-a417-46ec-83fe-a2358af38828'),
                               ('ari', ARI_RECEIPT), ('ari', ARI_RECEIPT), ('ari', ARI_RECEIPT)]:
                budget.reserve(request(rid), actor, 'workshop', '9fd12661')
            summary = request(ARI_RECEIPT)
            summary['messages'].append({'role': 'tool', 'content': json.dumps({
                'ok': True, 'result': {'number': 2, 'read_effect': 'processed_frozen_receipt'}})})
            with self.assertRaisesRegex(SampleStopped, 'unsettled_after_calls'):
                budget.reserve(summary, 'ari', 'workshop', '9fd12661')
            self.assertEqual(len(ledger), 5)
            self.assertEqual(sum(r['actor'] == 'ari' for r in ledger), 4)
            stop = json.loads((Path(temp) / 'STOP.json').read_text())
            self.assertEqual(stop['receipt_id'], ARI_RECEIPT)
            self.assertEqual(stop['cap'], 4)

    def test_short_handles_are_scoped_to_native_input_without_resetting_any_counter(self):
        self.assertNotEqual(frozen_receipt(request('r1', 0)), frozen_receipt(request('r1', 1)))
        self.assertNotEqual(frozen_receipt(request('r1', 0)), frozen_receipt(request('r1', 0, 'other-room')))
        with tempfile.TemporaryDirectory() as temp:
            ledger = []
            budget = TrialBudget(temp, ledger, allowance=29)
            for target in (0, 1):
                for _ in range(4): budget.reserve(request('r1', target), 'ari', 'workshop', 'candidate')
            with self.assertRaisesRegex(SampleStopped, 'unsettled_after_calls'):
                budget.reserve(request('r1', 1), 'ari', 'workshop', 'candidate')
            self.assertEqual(len(ledger), 8)

    def test_short_handle_without_exact_room_and_input_cannot_reserve(self):
        data = request('r1')
        data['messages'][0]['content'] = json.dumps({'receipt_id': 'r1'})
        with self.assertRaises(SampleStopped): frozen_receipt(data)


if __name__ == '__main__': unittest.main()
