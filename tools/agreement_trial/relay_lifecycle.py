"""Keep native clients alive until their already-reserved replies are delivered."""
from contextlib import contextmanager
import json
import threading
from trial_limits import SampleStopped


class RelayDrain:
    def __init__(self):
        self.condition = threading.Condition()
        self.active = 0
        self.closed = False

    @contextmanager
    def request(self):
        with self.condition:
            if self.closed:
                raise SampleStopped('Relay intake closed; no new model request')
            self.active += 1
        try:
            yield
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()

    def close_intake(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()

    def wait_idle(self, timeout=270):
        with self.condition:
            if not self.condition.wait_for(lambda: self.active == 0, timeout):
                raise TimeoutError('Reserved relay calls did not drain; delivery unconfirmed')


def stop_after_drain(activity, stop, timeout=270):
    activity.close_intake()
    activity.wait_idle(timeout)
    stop()


def deliver_response(handler, result, row, persist):
    # Persist the exact model action BEFORE attempting a possibly lost response.
    row.update(response=json.loads(result), native_delivery='pending')
    persist()
    try:
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Content-Length', str(len(result)))
        handler.end_headers()
        handler.wfile.write(result)
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        row['native_delivery'] = 'unconfirmed_disconnected'
        persist()
        raise SampleStopped('Native client disconnected; retained exact response, no automatic replay') from None
    row['native_delivery'] = 'response_written'
    persist()
