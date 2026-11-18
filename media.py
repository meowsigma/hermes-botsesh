"""DM P2 media (PARITY.md section 5): files, photos and voice between a DM and Hermes.

Inbound: attachments on the DM person's current inputs are downloaded as exact bytes
(length + server SHA-256 verified) into the gateway's own media cache, then handed to
Hermes as ``media_urls``/``media_types`` (images go to vision, voice notes to Hermes's
STT on this machine, other files become cached documents). Types come from the bytes,
never from the file name; a name never makes bytes an image or audio.

Outbound: ``send_image_file``/``send_document``/``send_video``/``send_voice``/
``send_multiple_images`` post one DM message with up to five exact files through the
owner-DM-only upload route. The UUID is derived from the exact intent, so a retry after
an uncertain transport reuses it and the server dedupes; nothing is re-minted.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import re
from pathlib import Path

from .client import BotSeshError

logger = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 10 * 1024 * 1024  # the room's per-message upload cap
MAX_FILES_PER_MESSAGE = 5
MAX_TURN_FILES = 10
MAX_TURN_BYTES = 25 * 1024 * 1024


def safe_name(name):
    """The server's stored attachment name (``attachmentName``), so digests/names compare exactly."""
    name = re.sub(r'[\\/\x00-\x1f\x7f]', '_', os.path.basename(str(name or '')))
    return name[:180] or 'download'


def sniff(data, name='', *, voice=False):
    """MIME from the bytes. A name only refines a sniffed container (m4a vs mp4) or names a
    plain document; it never turns unknown bytes into image/audio/video."""
    head = data[:16]
    ext = Path(name).suffix.lower()
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if head[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if head[:4] == b'OggS':
        return 'audio/ogg'
    if head[:4] == b'\x1a\x45\xdf\xa3':
        return 'audio/webm' if voice or ext in ('.weba', '.opus') else 'video/webm'
    if data[4:8] == b'ftyp':
        return 'audio/mp4' if voice or ext in ('.m4a', '.aac') else 'video/mp4'
    if head[:3] == b'ID3' or len(head) > 1 and head[0] == 0xff and (head[1] & 0xe0) == 0xe0:
        return 'audio/mpeg'
    if head[:4] == b'RIFF' and data[8:12] == b'WAVE':
        return 'audio/wav'
    if head[:4] == b'fLaC':
        return 'audio/flac'
    if head[:5] == b'%PDF-':
        return 'application/pdf'
    guessed = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    return 'application/octet-stream' if guessed.split('/')[0] in ('image', 'audio', 'video') else guessed


_EXT = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp',
        'audio/ogg': '.ogg', 'audio/webm': '.webm', 'audio/mp4': '.m4a', 'audio/mpeg': '.mp3',
        'audio/wav': '.wav', 'audio/flac': '.flac', 'video/webm': '.webm', 'video/mp4': '.mp4'}


def _cache(data, name, mime):
    from gateway.platforms import base
    kind = mime.split('/')[0]
    if kind == 'image':
        return base.cache_image_from_bytes(data, _EXT[mime])
    if kind == 'audio':
        return base.cache_audio_from_bytes(data, _EXT[mime])
    if kind == 'video':
        return base.cache_video_from_bytes(data, _EXT[mime])
    return base.cache_document_from_bytes(data, safe_name(name))


def _gateway_limit():
    try:
        from gateway.platforms.base import get_inbound_media_max_bytes
        return max(0, int(get_inbound_media_max_bytes()))
    except Exception:
        return MAX_TURN_BYTES


async def inbound(client, room_id, messages):
    """Download the DM person's attachments for this turn. Returns a dict with ``media_urls``,
    ``media_types``, ``message_type`` (a MessageType name) and ``files`` (model-visible
    provenance), or None when the inputs carry no attachment."""
    files, urls, types, total = [], [], [], 0
    budget = min(MAX_TURN_BYTES, _gateway_limit() or MAX_TURN_BYTES)
    for message in messages:
        if message.get('isOwn'):
            continue
        author = message.get('authorParticipant') or {}
        for attachment in message.get('attachments') or []:
            row = {'message_number': message.get('number'), 'name': attachment.get('name'),
                   'size': attachment.get('size'), 'provenance': 'room_data',
                   'uploaded_by': {'id': author.get('id'), 'kind': author.get('kind')}}
            files.append(row)
            if attachment.get('voice') is True:
                row['voice_note'] = True
            size = attachment.get('size') if isinstance(attachment.get('size'), int) else MAX_MESSAGE_BYTES + 1
            if len(urls) >= MAX_TURN_FILES or size > MAX_MESSAGE_BYTES or total + size > budget:
                row['delivered'] = False
                row['reason'] = 'over this turn\u2019s file limit'
                continue
            try:
                got = await asyncio.to_thread(client.enrolled, room_id, 'download_file', file_id=attachment.get('id'))
                data, digest = got['data'], got['sha256']
            except BotSeshError as error:
                if error.terminal:
                    raise
                logger.warning('BotSesh DM attachment not delivered (%s).', error.code)
                row['delivered'] = False
                row['reason'] = 'download failed (%s)' % error.code
                continue
            mime = sniff(data, attachment.get('name') or '', voice=attachment.get('voice') is True)
            try:
                path = await asyncio.to_thread(_cache, data, attachment.get('name') or 'file', mime)
            except ValueError as error:  # the gateway's own size/type refusal
                logger.warning('BotSesh DM attachment not cached: %s', type(error).__name__)
                row['delivered'] = False
                row['reason'] = 'refused by the gateway media cache'
                continue
            total += len(data)
            urls.append(path)
            types.append(mime)
            row.update(delivered=True, sha256=digest, type=mime)
    if not files:
        return None
    kinds = [t.split('/')[0] for t in types]
    if any(f.get('voice_note') and f.get('delivered') for f in files):
        message_type = 'VOICE'
    elif kinds and all(k == 'image' for k in kinds):
        message_type = 'PHOTO'
    elif 'video' in kinds:
        message_type = 'VIDEO'
    elif kinds and all(k == 'audio' for k in kinds):
        message_type = 'AUDIO'
    else:
        message_type = 'DOCUMENT' if kinds else 'TEXT'
    return {'media_urls': urls, 'media_types': types, 'message_type': message_type, 'files': files}


def read_local(paths, names=None):
    """Exact bytes of local regular files (no symlink), within the per-message cap."""
    out, total = [], 0
    for index, value in enumerate(paths):
        path = Path(value)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise BotSeshError('media_path_denied', rejected=True)
        data = path.read_bytes()
        total += len(data)
        if total > MAX_MESSAGE_BYTES:
            raise BotSeshError('file_too_large', 413, rejected=True)
        name = (names[index] if names and index < len(names) and names[index] else path.name)
        out.append((safe_name(name), data))
    return out


def upload_identity(client, room_id, files, *, text, voice, reply_to, scope):
    """One stable UUID per exact media intent (same bytes, caption, flag, thread and turn)."""
    return client.logical_id('dm-media', {'room': room_id, 'files': [[n, hashlib.sha256(d).hexdigest()] for n, d in files],
                                          'text': text or '', 'voice': bool(voice), 'reply_to': reply_to, 'scope': scope})


def upload(client, room_id, files, *, text='', voice=False, reply_to=None, scope=None):
    """Post one DM message with these exact files (blocking; call via to_thread)."""
    client_id = upload_identity(client, room_id, files, text=text, voice=voice, reply_to=reply_to, scope=scope)
    return client.enrolled(room_id, 'upload_files', client_id=client_id, files=files, text=text or '', reply_to=reply_to, voice=voice)
