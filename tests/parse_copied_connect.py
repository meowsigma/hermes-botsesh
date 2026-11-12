"""Parse the web's copied connect command without installing or running it."""
import argparse
import importlib.util
import json
from pathlib import Path
import shlex
import sys

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hyphen_botsesh', root / '__init__.py', submodule_search_locations=[str(root)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from hyphen_botsesh.cli import setup


def parse(command):
    argv = shlex.split(command)
    start = next(i for i in range(len(argv) - 2) if argv[i:i + 3] == ['hermes', 'botsesh', 'connect'])
    parser = argparse.ArgumentParser()
    setup(parser)
    return parser.parse_args(argv[start + 2:])


if __name__ == '__main__':
    args = parse(sys.stdin.read())
    print(json.dumps({'action': args.botsesh_action, 'room': args.room}))
