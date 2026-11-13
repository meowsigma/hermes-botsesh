"""Offline replay of call15's authored pass and shutdown-before-response race."""
import gzip
import json
import hashlib
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'agreement_trial'))
from relay_lifecycle import RelayDrain, stop_after_drain, deliver_response
from trial_limits import TrialBudget, SampleStopped, PER_SAMPLE_CAP, UNSETTLED_CALL_CAP
from test_receipt_failure_replay import capture, FIXTURES


def native_request(n):
    record = capture(n)
    return {'messages':record['native_messages'], 'tools':record['native_tools']}


def captured_pass_response():
    with gzip.open(FIXTURES / 'call-15-raw-tool-items.json.gz', 'rt') as stream:
        events = json.load(stream)
    raw = next(e['params'] for e in events if e.get('method') == 'item/tool/call')
    with gzip.open(FIXTURES / 'call-15-ledger.json.gz', 'rt') as stream:
        row = json.load(stream)
    assert row['error'] == 'BrokenPipeError'
    response = row['response']
    call = response['choices'][0]['message']['tool_calls'][0]
    assert call['id'] == raw['callId']
    assert call['function']['name'] == raw['tool']
    assert json.loads(call['function']['arguments']) == raw['arguments']
    return response


class HarnessReplayTests(unittest.TestCase):
    def test_trial_catalog_requires_external_exact_input_before_any_capture(self):
        import capture_precondition as precondition
        with patch.dict(os.environ, {'BOTSESH_TRIAL_MODEL_CATALOG': ''}):
            with self.assertRaisesRegex(RuntimeError, 'Explicit absolute'):
                precondition.capture_preflight({})
        with tempfile.TemporaryDirectory() as temp:
            catalog = Path(temp) / 'external-catalog.json'
            raw = b'{"models": []}'
            catalog.write_bytes(raw)
            with patch.dict(os.environ, {'BOTSESH_TRIAL_MODEL_CATALOG': str(catalog)}), \
                    patch.object(precondition, 'CAPTURE_CATALOG_SHA256', hashlib.sha256(raw).hexdigest()):
                self.assertEqual(precondition.external_catalog(), catalog.resolve())
                catalog.write_bytes(raw + b' ')
                with self.assertRaisesRegex(RuntimeError, 'differs'):
                    precondition.capture_preflight({})

    def test_pass_in_flight_is_delivered_before_native_gateway_stop(self):
        waiting, release, entered = threading.Event(), threading.Event(), threading.Event()
        class ObservedDrain(RelayDrain):
            def wait_idle(self, timeout=5):
                waiting.set()
                return super().wait_idle(timeout)
        activity = ObservedDrain()
        server, native = socket.socketpair()
        server.settimeout(5); native.settimeout(5)
        self.addCleanup(server.close); self.addCleanup(native.close)
        writer = server.makefile('wb', buffering=0)
        self.addCleanup(writer.close)
        handler = SimpleNamespace(send_response=lambda _:None, send_header=lambda *_:None,
                                  end_headers=lambda:None, wfile=writer)
        response = captured_pass_response()
        payload = json.dumps(response).encode()
        row, errors, stop_events = {}, [], []
        snapshots = []
        def worker():
            try:
                with activity.request():
                    entered.set()
                    if not release.wait(5): raise TimeoutError('test barrier')
                    deliver_response(handler, payload, row, lambda:snapshots.append(dict(row)))
            except Exception as error: errors.append(error)
        def stop_native():
            stop_events.append(row.get('native_delivery'))
            native.shutdown(socket.SHUT_RDWR)
        def cleanup():
            try: stop_after_drain(activity, stop_native, timeout=5)
            except Exception as error: errors.append(error)
        sender = threading.Thread(target=worker)
        sender.start(); self.assertTrue(entered.wait(5))
        cleaner = threading.Thread(target=cleanup)
        cleaner.start(); self.assertTrue(waiting.wait(5))
        release.set(); sender.join(5); cleaner.join(5)
        self.assertFalse(sender.is_alive()); self.assertFalse(cleaner.is_alive())
        self.assertEqual(errors, [], repr(errors))
        self.assertEqual(row['native_delivery'], 'response_written')
        self.assertEqual(stop_events, ['response_written'])
        self.assertEqual(row['response'], response)
        self.assertEqual(json.loads(native.recv(len(payload)+1)), response)
        self.assertEqual(snapshots[0]['native_delivery'], 'pending')
        with self.assertRaises(SampleStopped):
            with activity.request(): self.fail('No new request during drain')

    def test_real_disconnect_is_retained_as_uncertain_not_success_or_resend(self):
        row, saved = {}, []
        handler = SimpleNamespace(send_response=lambda _:None, send_header=lambda *_:None,
            end_headers=lambda:None, wfile=SimpleNamespace(write=lambda _:(_ for _ in ()).throw(BrokenPipeError())))
        response = captured_pass_response()
        with self.assertRaisesRegex(SampleStopped, 'no automatic replay'):
            deliver_response(handler, json.dumps(response).encode(), row, lambda:saved.append(dict(row)))
        self.assertEqual(row['response'], response)
        self.assertEqual(row['native_delivery'], 'unconfirmed_disconnected')
        self.assertEqual(len(saved), 2)

    def test_hard_sample_cap_survives_restart_and_cannot_spill_into_next_sample(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = []
            budget = TrialBudget(temp, ledger, allowance=36)
            for n in range(PER_SAMPLE_CAP):
                # Different exact captured receipts model distinct native turns;
                # no completed contribution is used as settlement evidence.
                budget.reserve(native_request(1 if n%2 else 15), 'ari' if n%4<2 else 'bo', 'workshop', 'pin')
            restored = json.loads((Path(temp)/'model-ledger.json').read_text())
            budget = TrialBudget(temp, restored, allowance=36)
            with self.assertRaisesRegex(SampleStopped, 'per_sample_call_cap'):
                budget.reserve(native_request(3), 'bo', 'workshop', 'pin')
            self.assertEqual(len(restored), PER_SAMPLE_CAP)
            with self.assertRaises(SampleStopped):
                budget.reserve(native_request(1), 'ari', 'supper', 'pin')
            self.assertEqual(len(restored), PER_SAMPLE_CAP)

    def test_unsettled_receipt_stops_even_when_total_allowance_remains(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = []
            budget = TrialBudget(temp, ledger, allowance=36)
            for _ in range(UNSETTLED_CALL_CAP): budget.reserve(native_request(3), 'ari', 'workshop', 'pin')
            with self.assertRaisesRegex(SampleStopped, 'unsettled_after_calls'):
                budget.reserve(native_request(3), 'ari', 'workshop', 'pin')
            self.assertEqual(len(ledger), UNSETTLED_CALL_CAP)

    def test_zero_authorization_denies_before_reservation(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger=[]
            with self.assertRaisesRegex(SampleStopped, 'authorized_total_call_cap'):
                TrialBudget(temp, ledger).reserve(capture(1), 'ari', 'workshop', 'pin')
            self.assertEqual(ledger, [])


if __name__ == '__main__': unittest.main()
