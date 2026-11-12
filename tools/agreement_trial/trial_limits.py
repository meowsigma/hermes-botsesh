"""Hard sample/receipt budgets; every reservation counts, including failures."""
import json
import re
import time
from pathlib import Path

PER_SAMPLE_CAP = 12
UNSETTLED_CALL_CAP = 4
SCENARIOS = 3
RERUN_BUDGET = PER_SAMPLE_CAP * SCENARIOS

class SampleStopped(RuntimeError):
    pass


def frozen_receipt(data):
    for message in reversed(data['messages']):
        if message.get('role') != 'user' or not isinstance(message.get('content'), str):
            continue
        try:
            packet, _ = json.JSONDecoder().raw_decode(message['content'].lstrip())
        except ValueError:
            continue
        if isinstance(packet, dict) and isinstance(packet.get('receipt_id'), str) and packet['receipt_id']:
            if re.fullmatch(r'r[1-9][0-9]*', packet['receipt_id']):
                room, target = packet.get('room_id'), packet.get('addressed_message_number')
                if not isinstance(room, str) or not room or type(target) is not int or target < 0:
                    raise SampleStopped('Short receipt needs exact native room/input scope')
                # r1 is a per-input alias, not a globally unique receipt ID.
                # This is a counting key only, never transport/read authority.
                return json.dumps([room, target, packet['receipt_id']], separators=(',', ':'))
            return packet['receipt_id']
    raise SampleStopped('No exact frozen receipt in native request; no inference authorized')


class TrialBudget:
    # Caller holds the relay lock across reserve and ledger persistence.
    def __init__(self, evidence, ledger, allowance=0):
        if type(allowance) is not int or not 0 <= allowance <= RERUN_BUDGET:
            raise ValueError('Explicit authorized budget must be between 0 and 36')
        self.evidence = Path(evidence)
        self.ledger = ledger
        self.allowance = allowance

    def stop(self, reason, **details):
        result = dict(reason=reason, calls=len(self.ledger), **details)
        (self.evidence / 'STOP.json').write_text(json.dumps(result))
        raise SampleStopped(json.dumps(result))

    def reserve(self, data, actor, sample, source_sha):
        if (self.evidence/'STOP.json').exists() or (self.evidence/'INVALID.json').exists():
            raise SampleStopped('Sample stopped; no retry')
        rows = [r for r in self.ledger if r.get('phase') == sample]
        if len(self.ledger) >= self.allowance:
            self.stop('authorized_total_call_cap', allowance=self.allowance)
        if len(rows) >= PER_SAMPLE_CAP:
            self.stop('per_sample_call_cap', sample=sample, cap=PER_SAMPLE_CAP)
        try:
            receipt = frozen_receipt(data)
        except SampleStopped:
            self.stop('missing_exact_frozen_receipt', sample=sample, actor=actor)
        pending = [r for r in rows if r.get('actor') == actor and r.get('receipt_id') == receipt]
        if len(pending) >= UNSETTLED_CALL_CAP:
            self.stop('unsettled_after_calls', sample=sample, actor=actor, receipt_id=receipt, cap=UNSETTLED_CALL_CAP)
        row = dict(n=len(self.ledger)+1, actor=actor, phase=sample, receipt_id=receipt,
                   source_sha=source_sha, started=time.time(),
                   tools=[x['function']['name'] for x in data.get('tools', [])],
                   previous_calls=[c for m in data['messages'] if m.get('role') == 'assistant' for c in m.get('tool_calls', [])])
        self.ledger.append(row)
        (self.evidence/'model-ledger.json').write_text(json.dumps(self.ledger, indent=2))
        return row
