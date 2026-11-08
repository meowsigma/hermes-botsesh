"""DM-only native command ingress. Room prose never acquires operator authority."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import logging
import time
import uuid

from gateway.platforms.base import SendResult
from gateway.platforms.event import MessageEvent, MessageType
from .command_catalog import build_manifest
from .dm_controls import DmControlsMixin
from .client import BotSeshError

_NATIVE_INPUT = ContextVar('botsesh_native_dm_input', default=None)
logger = logging.getLogger(__name__)


class CommandTransportMixin(DmControlsMixin):
    # DM consent is a narrow server-verified allowlist, not an allow-all or a
    # group invitation. Core still applies explicit operator allowlists first.
    enforces_own_access_policy = True
    _dm_policy = 'allowlist'

    def _consented_owner(self, chat_id, user_id):
        context = getattr(self, '_dm_contexts', {}).get(chat_id) or {}
        live = context.get('operator_projection') or {}
        try:
            room = self.client.store.room(chat_id)
        except Exception:
            return False
        return (bool(user_id) and room.get('active') is True
                and room.get('transport') == 'oauth' and room.get('room_type') == 'dm'
                and bool(room.get('oauth_id')) and context.get('until', 0) > time.monotonic()
                and context.get('human_id') == user_id == live.get('human_participant_id')
                and live.get('room_id') == chat_id and live.get('room_type') == 'dm'
                and live.get('participant_id') == room.get('participant_id')
                and bool(live.get('membership_id'))
                and live.get('membership_id') == context.get('membership_id')
                and live.get('human_owns_grant') is True and live.get('access_state') == 'authorized')

    def _is_dm_allowed(self, user_id):
        # Participant IDs are membership identities, never account/display names.
        return any(self._consented_owner(chat, user_id)
                   for chat in getattr(self, '_dm_contexts', {}))

    def _is_sender_authorized(self, user_id, chat_type=None, chat_id=None, **kwargs):
        if (chat_type == 'dm' and self._is_dm_allowed(user_id)
                and not self._consented_owner(chat_id, user_id)):
            return False
        return super()._is_sender_authorized(user_id, chat_type, chat_id, **kwargs)

    def _control_context(self, chat_id):
        room = self.client.store.room(chat_id)
        context = getattr(self, '_dm_contexts', {}).get(chat_id)
        if not room.get('active') or not context or context['until'] <= time.monotonic():
            return None
        result = {key: context[key] for key in ('human_id', 'membership_id', 'generation', 'session_key')}
        store = getattr(self, '_session_store', None)
        lookup = getattr(store, 'lookup_by_session_key', None)
        if callable(lookup):
            try:
                entry = lookup(context['session_key'])
                # /resume and native session pickers can rotate a route without
                # passing through our /new ingress. Bind the actual session too.
                result['generation'] = (context['generation'], getattr(entry, 'session_id', None))
            except Exception:
                return None
        return result

    async def _refresh_dm_controls(self, chat_id):
        room = self.client.store.room(chat_id)
        if room.get('transport') != 'oauth' or room.get('room_type') != 'dm':
            return
        if not hasattr(self, '_dm_contexts'):
            self._dm_contexts = {}
            self._dm_manifest_at = {}
            self._dm_manifests = {}
        prior = self._dm_contexts.get(chat_id)
        try:
            live = await asyncio.to_thread(self.client.enrolled, chat_id, 'dm_context')
            human, member = live.get('human_participant_id'), live.get('membership_id')
            if (live.get('room_type') != 'dm' or not human or not member
                    or live.get('participant_id') != room.get('participant_id')):
                raise BotSeshError('dm_identity_unavailable')
        except Exception:
            self._dm_contexts.pop(chat_id, None)  # Disable callbacks before any await.
            await self._control_invalidate(chat_id, 'DM access changed. No action was taken.')
            raise
        if prior and (prior['human_id'], prior['membership_id']) != (human, member):
            self._dm_contexts.pop(chat_id, None)
            await self._control_invalidate(chat_id)
            prior = None
        source = self.build_source(chat_id=chat_id, chat_name=room.get('name', chat_id),
                                   chat_type='dm', user_id=human, user_name='DM owner')
        self._dm_contexts[chat_id] = dict(human_id=human, membership_id=member,
            generation=prior['generation'] if prior else str(uuid.uuid4()),
            session_key=self._source_session_key(source), until=time.monotonic()+35,
            operator_projection={key: live.get(key) for key in ('room_id', 'room_type', 'participant_id',
                'membership_id', 'human_participant_id', 'human_owns_grant', 'access_state')})
        # Native Hermes remains the authority. Its own-policy allowlist can now
        # admit only this fresh grant-owner projection after explicit DM consent;
        # operator-configured native allowlists are never edited.
        if self._is_sender_authorized(human, 'dm', chat_id) is not True:
            return
        if time.monotonic() - self._dm_manifest_at.get(chat_id, 0) >= 30:
            try:
                await self._publish_dm_manifest(chat_id, member)
            except Exception as error:
                # A failed/oversized menu must not wedge native typed commands.
                logger.warning('BotSesh DM manifest unavailable (%s); native authorized commands remain available.', type(error).__name__)
            finally:
                self._dm_manifest_at[chat_id] = time.monotonic()

    async def _publish_dm_manifest(self, chat_id, member):
        commands = build_manifest()
        previous = self._dm_manifests.get(chat_id)
        # Retain one exact in-flight refresh; idle refreshes do not fill the
        # durable operation journal (or resurrect an older catalog on A→B→A).
        if previous and previous['member'] == member and previous['confirmed'] and previous['payload']['commands'] == commands:
            return
        if previous and previous['member'] == member and not previous['confirmed']:
            payload = previous['payload']
        else:
            payload = dict(version=1, clientId=str(uuid.uuid4()), commands=commands)
            self._dm_manifests[chat_id] = dict(member=member, payload=payload, confirmed=False)
        response = await asyncio.to_thread(self.client.enrolled, chat_id, 'dm_control', action='manifest', payload=payload)
        projected = (response.get('message') or {}).get('dmControl') or {}
        if projected.get('commands') != payload['commands'] or projected.get('agentMembershipId') != member:
            raise BotSeshError('manifest_readback_mismatch')
        self._dm_manifests[chat_id]['confirmed'] = True

    async def _control_call(self, chat_id, action, payload):
        if action not in ('send', 'update', 'retire') or self._control_context(chat_id) is None:
            raise BotSeshError('dm_control_denied')
        return await asyncio.to_thread(self.client.enrolled, chat_id, 'dm_control', action=action, payload=payload)

    async def _control_notice(self, chat_id, text):
        incoming = _NATIVE_INPUT.get()
        target = incoming[1] if incoming and incoming[0] == chat_id else None
        payload = dict(text=text, client_id=self.client.logical_id('dm-control-notice',
            {'room': chat_id, 'target': target, 'text': text}))
        if target is not None:
            payload['reply_to'] = target
        result = await self._post_payload(chat_id, payload)
        return SendResult(success=True, message_id=str(result['number']))

    async def _control_edit(self, chat_id, message_id, content, *, finalize=False):
        try:
            if self._control_context(chat_id) is None:
                return SendResult(success=False, error='Not an active DM')
            result = await asyncio.to_thread(self.client.enrolled, chat_id, 'edit', number=int(message_id), text=content,
                client_id=self.client.logical_id('native-edit', {'room': chat_id, 'number': message_id, 'text': content}))
            return SendResult(success=True, message_id=str(result['message']['number']))
        except Exception:
            return SendResult(success=False, error='Message edit unconfirmed; no replacement edit was sent')

    async def _control_ingest(self, chat_id, message):
        """Return True only for a DM choice, command, or native typed-prompt reply."""
        context = self._control_context(chat_id)
        choice = (message.get('dmControl') or {}).get('kind') == 'choice'
        if not context:
            return choice  # Never promote even an unauthorized choice to model text.
        author = message.get('authorParticipant') or {}
        human = context['human_id']
        if (message.get('senderParticipantId') != human or author.get('id') != human
                or author.get('verified') is not True
                or self._is_sender_authorized(human, 'dm', chat_id) is not True):
            return choice
        text = message.get('text', '')
        source = self.build_source(chat_id=chat_id, chat_name=self.client.store.room(chat_id).get('name', chat_id),
            chat_type='dm', user_id=human, user_name=author.get('name') or 'DM owner')
        event = MessageEvent(text=text, source=source, message_id=str(message['number']), user_id=human,
            message_type=MessageType.TEXT, raw_message=message, allow_gateway_control=True,
            reply_expected=True, metadata={'botsesh_control': True})
        from tools.clarify_gateway import get_pending
        session_key = self._source_session_key(source)
        clarify_id = getattr(self, '_dm_pending_clarify', {}).get(session_key)
        pending = get_pending(clarify_id) if clarify_id else None
        typed = not event.is_command() and pending is not None and pending.session_key == session_key
        if not (choice or event.is_command() or typed):
            return False
        target = message['number']
        seen = self.client.store.room(chat_id).get('control_inputs', {})
        # At-most-once native dispatch. Restart cannot recover a callback or replay
        # a possibly executed shell quick-command. Uncertain runs stay honest.
        if str(target) in seen:
            await self._delivery(chat_id, target, 'handled')
            return True
        def remember(data):
            rows = data['rooms'][chat_id].setdefault('control_inputs', {})
            rows[str(target)] = {'received_at': time.time()}
            while len(rows) > 1000:
                rows.pop(next(iter(rows)))
        self.client.store.update(remember)
        token = _NATIVE_INPUT.set((chat_id, target))
        try:
            if choice:
                await self._control_receive(chat_id, message)
            elif event.get_command() == 'name':
                value = text.split(None, 1)[1].strip() if len(text.split(None, 1)) == 2 else None
                try:
                    result = await asyncio.to_thread(self.client.enrolled, chat_id, 'name', display_name=value)
                    await self._control_notice(chat_id, 'My addressable name in this room is @' + result['display_name'] + '.')
                except BotSeshError as error:
                    await self._control_notice(chat_id, 'Name not changed (' + error.code + '). Choose a unique room name of 1–40 characters; a person’s name cannot be used.')
            elif event.is_command() and not event.get_command():
                await self._control_notice(chat_id, 'Choose a command from the / menu, or send /commands for the native list. No model was called for this incomplete command.')
            else:
                if event.get_command() in ('new', 'reset', 'stop'):
                    self._dm_contexts[chat_id]['generation'] = str(uuid.uuid4())
                    await self._control_invalidate(chat_id)
                await self.handle_message(event)  # The installed native dispatcher, never a prompt wrapper.
            await self._delivery(chat_id, target, 'handled')  # Transport handling, not a read/answer attestation.
        finally:
            _NATIVE_INPUT.reset(token)
        return True
