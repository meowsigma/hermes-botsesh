"""Run scripted native start/eligibility checks on an exact local server image."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-root', required=True, type=Path)
    parser.add_argument('--image', required=True)
    parser.add_argument('--evidence-file', required=True, type=Path)
    args = parser.parse_args()
    if args.evidence_file.exists():
        parser.error('Choose a new append-only evidence file')
    docker = shutil.which('docker')
    if not docker:
        parser.error('Docker is required for the local server only')
    tests = Path(__file__).resolve().parent
    command = [docker, 'run', '--rm', '-i', '--network', 'host',
               '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m',
               '-v', str(tests / 'oauth-connect-server.mjs') + ':/fixture.mjs:ro',
               '--entrypoint', 'node', args.image, '/fixture.mjs', '/app/app.js']
    with tempfile.TemporaryDirectory(prefix='native-start-empty-path-') as empty:
        env = {**os.environ, 'PATH': empty, 'PYTHONPATH': str(args.hermes_root.resolve()),
               'BOTSESH_OAUTH_CONNECT_SERVER_COMMAND': json.dumps(command),
               'BOTSESH_NATIVE_START_EVIDENCE': str(args.evidence_file.resolve())}
        env.pop('BOTSESH_OAUTH_CONNECT_EVIDENCE', None)
        return subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(tests),
                               '-p', 'test_native_started_at.py', '-v'], env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
