"""Run only the invitation regression against an explicit local server image.

No package installs, credentials, production account or inference. Node exists
only inside the server container; Python client PATH is a fresh empty directory.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-root',required=True,type=Path)
    parser.add_argument('--image',required=True,help='Exact already-built local BotSesh server image')
    parser.add_argument('--evidence-file',type=Path)
    args=parser.parse_args()
    docker=shutil.which('docker')
    if not docker:
        parser.error('Docker is required for the local server fixture, not the plugin client')
    tests=Path(__file__).resolve().parent
    fixture=tests/'python-invitation-server.mjs'
    server=[docker,'run','--rm','-i','--network','host','--tmpfs','/tmp:rw,nosuid,nodev,size=256m',
            '-v',str(fixture)+':/fixture.mjs:ro','--entrypoint','node',args.image,'/fixture.mjs','/app/app.js']
    with tempfile.TemporaryDirectory(prefix='invitation-empty-path-') as empty:
        env={**os.environ,'PATH':empty,'PYTHONPATH':str(args.hermes_root.resolve()),
             'BOTSESH_INVITATION_SERVER_COMMAND':json.dumps(server)}
        if args.evidence_file:
            if args.evidence_file.exists():
                parser.error('Choose a new evidence file; results are append-only per run')
            env['BOTSESH_INVITATION_EVIDENCE']=str(args.evidence_file.resolve())
        else:
            env.pop('BOTSESH_INVITATION_EVIDENCE',None)
        return subprocess.run([sys.executable,'-m','unittest','discover','-s',str(tests),
                               '-p','test_python_invitation.py','-v'],env=env).returncode


if __name__=='__main__':
    raise SystemExit(main())
