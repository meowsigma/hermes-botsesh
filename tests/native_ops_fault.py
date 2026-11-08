"""Opt-in container-only lifecycle fault, never used by product code.

The second restart first performs a supported non-forcing gateway stop, then
fails. This models a lifecycle client failing after shutdown and proves that
the plugin's recovery actually starts an absent owner instead of only printing
that config was restored. All other calls go to the real native core.
"""
import os
from pathlib import Path
import subprocess
import sys

assert os.environ['HOME'] == '/task/native/os-home'
assert os.environ['HERMES_HOME'] == '/task/native/home'
args = sys.argv[1:]
if args[-3:] == ['gateway', 'restart', '--detach']:
    counter = Path('/task/native/recovery-restart-count')
    count = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(count))
    if count == 2:
        stopped = subprocess.run([sys.executable, '-m', 'hermes_cli.main', *args[:-2], 'stop'], stdin=subprocess.DEVNULL)
        assert stopped.returncode == 0
        Path('/task/native/recovery-stop-injected').write_text('supported stop completed')
        sys.exit(1)
os.execv(sys.executable, [sys.executable, '-m', 'hermes_cli.main', *args])
