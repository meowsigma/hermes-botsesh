"""One-shot dry preflight; failures are latched before queued entry."""
import json
import threading

class DryCaptureGuard:
    def __init__(self, evidence):
        self.evidence = evidence
        self.lock = threading.Lock()
        self.started = False
        self.failed = False
        self.invocations = 0

    def run(self, data, preflight):
        with self.lock:
            if self.failed or (self.evidence/'STOP.json').exists() or (self.evidence/'dry-error.txt').exists():
                raise RuntimeError('Dry capture already failed/stopped; no re-entry')
            if self.started:
                assert (self.evidence/'dry-run.json').exists(), 'Missing successful one-shot result'
                return
            self.started = True
            self.invocations += 1
            try:
                preflight(data)
            except Exception as exc:
                self.failed = True
                # Both writes happen with the same lock held. Queued callers
                # cannot enter preflight between the exception and these latches.
                (self.evidence/'dry-error.txt').write_text(repr(exc))
                (self.evidence/'STOP.json').write_text(json.dumps({'reason':repr(exc),'dry_preflight_failed':True,'capture_invocations':self.invocations}))
                raise
            finally:
                (self.evidence/'dry-guard-count.json').write_text(json.dumps({'total_capture_invocations':self.invocations,'one_shot':self.invocations==1,'started':self.started,'failed':self.failed},indent=2))
