"""Native no-Node OAuth connect checks against an explicit local server image."""
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
    parser.add_argument('--evidence-file', type=Path, required=True)
    parser.add_argument('--test', help='Optional exact unittest method substring')
    args = parser.parse_args()
    if args.evidence_file.exists():
        parser.error('Choose a new append-only evidence file')
    docker = shutil.which('docker')
    if not docker:
        parser.error('Docker is required only for the local server')
    tests = Path(__file__).resolve().parent
    command = [docker, 'run', '--rm', '-i', '--network', 'host',
               '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m',
               '-v', str(tests / 'oauth-connect-server.mjs') + ':/fixture.mjs:ro',
               '--entrypoint', 'node', args.image, '/fixture.mjs', '/app/app.js']
    with tempfile.TemporaryDirectory(prefix='oauth-empty-path-') as empty:
        env = {**os.environ, 'PATH': empty, 'PYTHONPATH': str(args.hermes_root.resolve()),
               'BOTSESH_OAUTH_CONNECT_SERVER_COMMAND': json.dumps(command),
               'BOTSESH_OAUTH_CONNECT_EVIDENCE': str(args.evidence_file.resolve())}
        call = [sys.executable, '-m', 'unittest', 'discover', '-s', str(tests),
                '-p', 'test_oauth_connect.py', '-v']
        if args.test:
            call += ['-k', args.test]
        return subprocess.run(call, env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
