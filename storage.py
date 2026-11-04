"""Private, profile-relative metadata. Generated SDK credentials never enter tool output."""
from __future__ import annotations
import contextlib
import json
import os
from pathlib import Path
import stat
import tempfile
import threading

_LOCK = threading.RLock()

class StorageError(RuntimeError):
    pass

class Store:
    def __init__(self, root=None):
        if root is None:
            from plugins.plugin_storage import plugin_data_dir
            root = plugin_data_dir('botsesh')
        self.root = Path(root).absolute()
        for p in (self.root, *self.root.parents):
            if p.is_symlink():
                raise StorageError('Plugin data paths must not be symlinks.')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = self.root.stat()
        if hasattr(os, 'getuid') and st.st_uid != os.getuid():
            raise StorageError('Plugin data must belong to this user.')
        self.root.chmod(0o700)
        self.credentials = self.root / 'credentials'
        self.credentials.mkdir(mode=0o700, exist_ok=True)
        self._check(self.credentials, directory=True)
        self.path = self.root / 'rooms.json'

    @staticmethod
    def _check(path, directory=False):
        st = path.lstat()
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(st.st_mode) or stat.S_IMODE(st.st_mode) != (0o700 if directory else 0o600):
            raise StorageError('Private plugin storage has unsafe type or permissions.')
        if hasattr(os, 'getuid') and st.st_uid != os.getuid():
            raise StorageError('Private plugin storage has another owner.')

    @contextlib.contextmanager
    def locked(self):
        import fcntl
        with _LOCK:
            path = self.root / '.registry.lock'
            fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            try:
                self._check(path)
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    def _load(self):
        if not self.path.exists():
            if self.path.is_symlink():
                raise StorageError('Unsafe registry path.')
            return {'version': 1, 'rooms': {}}
        self._check(self.path)
        with os.fdopen(os.open(self.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), encoding='utf-8') as stream:
            data = json.load(stream)
        if data.get('version') != 1 or not isinstance(data.get('rooms'), dict):
            raise StorageError('Unsupported or damaged registry; preserve it and restore from backup.')
        return data

    def read(self):
        with self.locked():
            return self._load()

    def update(self, fn):
        with self.locked():
            data = self._load()
            fn(data)
            fd, name = tempfile.mkstemp(dir=self.root, prefix='.rooms-', suffix='.tmp')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump(data, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.path)
                directory = os.open(self.root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            return data

    def room(self, room_id):
        room = self.read()['rooms'].get(room_id)
        if not room or not room.get('active'):
            raise StorageError('Room is not locally enrolled or is detached. Use /botsesh join locally.')
        return room
