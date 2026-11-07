"""Transport-independent, process-local BotSesh DM controls (protocol v1).

Place before BasePlatformAdapter in the adapter MRO. All I/O is through the
three required adapter seams: async _control_call(chat_id, action, payload),
async _control_notice(chat_id, text), and sync _control_context(chat_id).
_context returns a verified active DM's human_id, membership_id, generation,
and optional session_key; None rejects the route. See INTEGRATION.md.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import time
from typing import Any, Callable
from uuid import uuid4

from gateway.platforms.base import ExecApprovalPrompt, SendResult


_NATIVE_COPY = {
    'gateway.clarify.skip_hint': 'Reply "skip" to skip this question.',
    'gateway.shutdown.notice_restart': 'Hermes is restarting.',
    'gateway.shutdown.notice_shutdown': 'Hermes is shutting down.',
}


def _native_translation(key: str) -> str:
    # Core can return the bare key when an installed language catalogue is absent.
    # Use its profile-scoped translator first; never infer just to translate UI.
    try:
        from agent.i18n import t
        text = t(key)
        if isinstance(text, str) and text.strip() and not text.startswith('gateway.'):
            return text
    except Exception:
        pass
    return _NATIVE_COPY[key]


def _clarify_text(question: str) -> str:
    # Only the core-appended hint is presentation. Keep the pending question,
    # choices, callback identity and authored text byte-for-byte unchanged.
    key = 'gateway.clarify.skip_hint'
    if question.endswith('\n' + key):
        return question[:-len(key)] + _native_translation(key)
    return question


def _shutdown_notice(text: str) -> bool:
    return any(text in (key, _NATIVE_COPY[key], _native_translation(key))
               for key in ('gateway.shutdown.notice_restart', 'gateway.shutdown.notice_shutdown'))


def _units(text: str) -> int:
    return len(text.encode('utf-16-le')) // 2


def _text(text: str, maximum: int, *, blank: bool = False) -> str:
    if not isinstance(text, str) or (not blank and not text.strip()):
        raise ValueError('Blank or non-string control text')
    if _units(text) > maximum or any((ord(c) < 32 and c not in '\t\n\r') or ord(c) == 127 for c in text):
        raise ValueError('Control text exceeds protocol limits or contains control characters')
    return text


def _label(text: str) -> str:
    # Labels are presentation only. Values and command previews are NEVER cut.
    text = str(text)
    while _units(text) > 159:
        text = text[:-1]
    return _text(text, 160)


@dataclass
class _Card:
    chat_id: str
    binding: tuple
    session_key: str
    kind: str
    intent: Any
    callback: Callable
    values: dict
    payload: dict
    expires: float
    message_id: str | None = None
    source_number: int | None = None
    claimed: bool = False
    sending: bool = False
    native_id: str | None = None
    native_valid: Callable | None = None


class DmControlsMixin:
    """No sockets, background tasks, persistence, model calls, or core mutation.

    Call _control_sweep periodically from the parent's existing poll loop and
    _control_invalidate at every new/reset/stop boundary. In-memory callbacks
    deliberately do not survive a process restart.
    """
    CONTROL_TTL = 300
    CONTROL_MAX_CARDS = 128
    CONTROL_MAX_OPERATIONS = 256

    def _controls(self):
        if not hasattr(self, '_dm_cards'):
            self._dm_cards = OrderedDict()
            self._dm_operations = OrderedDict()
        return self._dm_cards

    def _binding(self, chat_id, session_key=None):
        context = self._control_context(chat_id)
        if not isinstance(context, dict) or any(context.get(k) is None for k in ('human_id', 'membership_id', 'generation')):
            raise ValueError('Not a verified active DM')
        if not context['human_id'] or not context['membership_id']:
            raise ValueError('Missing verified DM identity')
        if session_key is not None and context.get('session_key') not in (None, session_key):
            raise ValueError('DM session changed')
        return (context['human_id'], context['membership_id'], context['generation'], context.get('session_key'))

    def _control_current(self, card):
        try:
            return (self._binding(card.chat_id, card.session_key) == card.binding
                    and card.expires > time.time()
                    and self._is_sender_authorized(card.binding[0], 'dm', card.chat_id) is True)
        except Exception:
            return False

    def _native_control_current(self, card):
        try:
            return card.native_valid is None or card.native_valid() is True
        except Exception:
            return False

    async def _op(self, chat_id, action, payload):
        """Retain one immutable body until an exact retry resolves uncertainty."""
        self._controls()
        client_id = payload['clientId']
        previous = self._dm_operations.get(client_id)
        if previous is not None and previous != (chat_id, action, payload):
            raise ValueError('Operation UUID payload changed')
        if previous is None and len(self._dm_operations) >= self.CONTROL_MAX_OPERATIONS:
            raise ValueError('Too many unresolved control operations')
        self._dm_operations[client_id] = (chat_id, action, deepcopy(payload))
        response = await self._control_call(chat_id, action, deepcopy(payload))
        if not isinstance(response, dict) or not isinstance(response.get('message'), dict):
            raise ValueError('Malformed control response; exact retry retained')
        if action != 'send':
            self._dm_operations.pop(client_id, None)
        return response

    async def _deliver_card(self, card):
        if card.sending:
            return SendResult(success=False, error='Control send already in flight')
        if not self._control_current(card) or not self._native_control_current(card) or card.expires <= time.time():
            return SendResult(success=False, error='Control expired; use the native text command')
        card.sending = True
        try:
            response = await self._op(card.chat_id, 'send', card.payload)
            message = response['message']
            number = message.get('number')
            control = message.get('dmControl') or {}
            if (type(number) is not int or number < 0 or control.get('callbackId') != card.payload['callbackId']
                    or control.get('agentMembershipId') != card.binding[1]
                    or control.get('version') != 1 or control.get('kind') != 'interactive'):
                # Preserve retry identity even when a malformed response cannot bind a card.
                self._dm_operations[card.payload['clientId']] = (card.chat_id, 'send', deepcopy(card.payload))
                return SendResult(success=False, error='Control response identity mismatch',
                                  raw_response={'control_client_id': card.payload['clientId']})
            self._dm_operations.pop(card.payload['clientId'], None)
            card.source_number = number
            card.message_id = str(number)  # Hermes edits use ROOM number, never database id.
            if control.get('status') != 'pending' or not self._control_current(card):
                card.claimed = True
            return SendResult(success=True, message_id=card.message_id, raw_response=response)
        except Exception:
            return SendResult(success=False, error='Control delivery uncertain; retry the retained control operation',
                              raw_response={'control_client_id': card.payload['clientId']})
        finally:
            card.sending = False

    async def _control_retry(self, client_id):
        """Explicit bounded same-body retry. Never invent a replacement card UUID."""
        self._controls()
        operation = self._dm_operations.get(client_id)
        if operation is None:
            return SendResult(success=False, error='Unknown or expired control operation')
        chat_id, action, payload = operation
        if action == 'send':
            card = self._dm_cards.get(payload['callbackId'])
            if card is None:
                return SendResult(success=False, error='Control expired')
            return await self._deliver_card(card)
        card = self._dm_cards.get(payload['callbackId'])
        if (card is None or not self._control_current(card)
                or (action == 'update' and (card.claimed or not self._native_control_current(card)))):
            return SendResult(success=False, error='Control expired')
        try:
            response = await self._op(chat_id, action, payload)
            return SendResult(success=True, message_id=card.message_id, raw_response=response)
        except Exception:
            return SendResult(success=False, error='Control operation uncertain', raw_response={'control_client_id': client_id})

    async def _publish(self, chat_id, session_key, kind, intent, text, rows, callback,
                       *, buttons=(), preview=None, ttl=None, native_id=None, binding=None, native_valid=None):
        try:
            cards = self._controls()
            current = self._binding(chat_id, session_key)
            if binding is not None and current != binding:
                raise ValueError('DM session changed')
            _text(text, 8000)
            if preview is not None:
                _text(preview, 4000, blank=True)
            if len(rows) > 64 or len(buttons) > 8 or not (rows or buttons):
                raise ValueError('Invalid control option count')
            # Only uncertain identical sends are recovered, not successful later invocations.
            for card in cards.values():
                if (card.chat_id == chat_id and card.binding == current and card.kind == kind
                        and card.intent == intent and card.payload['clientId'] in self._dm_operations):
                    return await self._deliver_card(card)
            await self._control_sweep()
            if len(cards) >= self.CONTROL_MAX_CARDS:
                raise ValueError('Too many active controls; use the native text command')
            seconds = min(self.CONTROL_TTL, ttl if ttl is not None and ttl > 0 else self.CONTROL_TTL)
            expires = time.time() + seconds
            payload = dict(version=1, clientId=str(uuid4()), callbackId=str(uuid4()), text=text,
                           expiresAt=int(expires * 1000), buttons=[], choices=[])
            if preview is not None:
                payload['preview'] = preview
            values = {}
            for field_name, options in (('choices', rows), ('buttons', buttons)):
                for label, value, style in options:
                    token = str(uuid4())
                    payload[field_name].append(dict(id=token, label=_label(label), style=style or 'secondary'))
                    values[token] = value
            card = _Card(chat_id, current, session_key, kind, intent, callback, values, payload, expires,
                         native_id=native_id, native_valid=native_valid)
            cards[payload['callbackId']] = card
            return await self._deliver_card(card)
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    async def _expired_notice(self, chat_id):
        await self._control_notice(chat_id, 'This control has expired or was already handled. Use the native command again; no action was taken from this tap.')

    async def _control_receive(self, chat_id, message):
        """Consume authentic server-projected choice rows BEFORE normal event admission.

        A caller must supply only authenticated REST/MCP message projections, not
        user JSON parsed from message.text. Protocol shape alone is not provenance.
        True means consumed, including rejected choice rows; never infer on them.
        """
        control = message.get('dmControl') if isinstance(message, dict) else None
        if not isinstance(control, dict) or control.get('kind') != 'choice':
            return False
        try:
            binding = self._binding(chat_id)
            participant = message.get('authorParticipant') or {}
            human = binding[0]
            if (type(control.get('version')) is not int or control['version'] != 1
                    or not isinstance(control.get('callbackId'), str)
                    or not isinstance(control.get('optionId'), str)
                    or control.get('agentMembershipId') != binding[1]
                    or message.get('senderParticipantId') != human
                    or participant.get('id') != human or participant.get('verified') is not True
                    or self._is_sender_authorized(human, 'dm', chat_id) is not True):
                return True
        except Exception:
            return True
        cards = self._controls()
        card = cards.get(control.get('callbackId'))
        if card is None:
            await self._expired_notice(chat_id)
            return True
        if card.chat_id != chat_id or card.binding != binding:
            return True
        source = control.get('sourceNumber')
        if type(source) is not int or source != card.source_number or control.get('optionId') not in card.values:
            return True
        if card.claimed:
            return True
        # Claim synchronously before any await, including stale notices/callbacks.
        card.claimed = True
        callback, card.callback = card.callback, None
        if card.expires <= time.time() or not self._control_current(card):
            await self._expired_notice(chat_id)
            return True
        try:
            await callback(card, card.values[control['optionId']])
        except Exception:
            await self._control_notice(chat_id, 'Control handling failed. Check /status before retrying; no replacement action was approved automatically.')
        return True

    async def _retire(self, card, notice):
        card.claimed = True
        card.callback = None
        payload = next((p for c, a, p in self._dm_operations.values()
                        if c == card.chat_id and a == 'retire' and p['callbackId'] == card.payload['callbackId']), None)
        if payload is None:
            payload = dict(version=1, clientId=str(uuid4()), callbackId=card.payload['callbackId'], notice=_text(notice, 4000, blank=True))
        try:
            await self._op(card.chat_id, 'retire', payload)
        except Exception:
            pass  # Local invalidation is authoritative even when the server is unavailable.

    async def edit_message(self, chat_id: str, message_id: str, content: str, *, finalize: bool = False) -> SendResult:
        card = next((c for c in self._controls().values()
                     if c.chat_id == chat_id and c.message_id == str(message_id)), None)
        if card is None:
            edit = getattr(self, '_control_edit', None)
            if edit is None:
                return SendResult(success=False, error='Ordinary message edits not supported')
            return await edit(chat_id, message_id, content, finalize=finalize)
        if card.claimed or not self._control_current(card) or card.expires <= time.time():
            return SendResult(success=False, error='Control is no longer editable; post a new notice')
        if not self._native_control_current(card):
            await self._retire(card, 'This native request is no longer waiting.')
            return SendResult(success=False, error='Native request ended; post a new notice')
        try:
            _text(content, 8000)
            payload = next((p for c, action, p in self._dm_operations.values()
                            if c == chat_id and action == 'update' and p['callbackId'] == card.payload['callbackId']
                            and p['text'] == content), None)
            if payload is None:
                payload = dict(version=1, clientId=str(uuid4()), callbackId=card.payload['callbackId'], text=content)
            response = await self._op(chat_id, 'update', payload)
            return SendResult(success=True, message_id=card.message_id, raw_response=response)
        except Exception:
            return SendResult(success=False, error='Control edit failed or uncertain; selected cards are immutable',
                              raw_response={'control_client_id': payload['clientId']} if 'payload' in locals() else None)

    async def _control_sweep(self):
        """Bounded retirement; parent calls periodically. Creates no tasks."""
        cards = self._controls()
        for token, card in list(cards.items()):
            if card.expires > time.time() and self._control_current(card) and self._native_control_current(card):
                continue
            if not card.claimed:
                await self._retire(card, 'This control is no longer active.')
            cards.pop(token, None)
            for client_id, (_, _, payload) in list(self._dm_operations.items()):
                if payload.get('callbackId') == token:
                    self._dm_operations.pop(client_id, None)

    async def _control_invalidate(self, chat_id, notice='Session changed. Use the native command again.'):
        """Parent MUST bump generation synchronously before awaiting this method."""
        for card in list(self._controls().values()):
            if card.chat_id == chat_id:
                if not card.claimed:
                    await self._retire(card, notice)
                card.expires = 0
        await self._control_sweep()

    async def send_choice_picker(self, chat_id: str, title: str, choices: list, session_key: str,
                                 on_choice_selected, metadata: dict[str, Any] | None = None) -> SendResult:
        if not choices:
            return SendResult(success=False, error='No choices')
        rows = [('✓ ' + str(c['label']) if c.get('is_current') else str(c.get('label') or c['value']),
                 c['value'], 'secondary') for c in choices]
        async def selected(card, value):
            if value[0] == 'page':
                await show(value[1], card.binding)
            elif value[0] == 'cancel':
                await self._control_notice(chat_id, 'Selection cancelled.')
            else:
                result = await on_choice_selected(chat_id, value[1])
                await self._control_notice(chat_id, result if isinstance(result, str) and result else 'Selection applied.')
        async def show(page, binding=None):
            start = page * 64
            buttons = [('Cancel', ('cancel',), 'secondary')]
            if page:
                buttons.append(('Previous', ('page', page - 1), 'secondary'))
            if start + 64 < len(rows):
                buttons.append(('Next', ('page', page + 1), 'secondary'))
            return await self._publish(chat_id, session_key, 'choice', (id(on_choice_selected), title, repr(choices), page),
                title + (f' (page {page + 1})' if len(rows) > 64 else ''),
                [(label, ('value', value), style) for label, value, style in rows[start:start + 64]],
                selected, buttons=buttons, binding=binding)
        return await show(0)

    async def send_clarify(self, chat_id: str, question: str, choices: list | None, clarify_id: str,
                           session_key: str, metadata: dict[str, Any] | None = None) -> SendResult:
        from tools import clarify_gateway as clarify
        try:
            binding = self._binding(chat_id, session_key)
            entry = clarify.get_pending(clarify_id)
            if (entry is None or entry.session_key != session_key or entry.question != question
                    or list(entry.choices or []) != list(choices or [])):
                raise ValueError('Clarify request is no longer pending or does not match')
            snapshot = (entry.question, tuple(entry.choices or []), entry.multi_select)
            display_question = _clarify_text(question)
            if not choices or entry.multi_select:
                # v1 is single-choice. Native typed parser retains multi-select semantics.
                clarify.mark_awaiting_text(clarify_id)
                text = display_question
                if choices:
                    text += '\n\n' + '\n'.join(f'{i}. {c}' for i, c in enumerate(choices, 1))
                    text += '\nType one or more option numbers (for example 1,2), or type your answer.'
                notice_result = await self._control_notice(chat_id, text)
                return notice_result if isinstance(notice_result, SendResult) else SendResult(success=True)
            async def selected(card, value):
                live = clarify.get_pending(clarify_id)
                if (live is not entry or (live.question, tuple(live.choices or []), live.multi_select) != snapshot):
                    await self._expired_notice(chat_id)
                    return
                if value[0] == 'other':
                    if clarify.mark_awaiting_text(clarify_id):
                        await self._control_notice(chat_id, 'Type your answer as your next message. The question is still waiting; this tap did not answer it.')
                    else:
                        await self._expired_notice(chat_id)
                elif clarify.resolve_gateway_clarify(clarify_id, value[1]):
                    await self._control_notice(chat_id, 'Answer submitted.')
                else:
                    await self._expired_notice(chat_id)
            result = await self._publish(chat_id, session_key, 'clarify', (clarify_id, id(entry)), display_question,
                [(str(c), ('answer', str(c)), 'secondary') for c in choices], selected,
                buttons=[('Other (type answer)', ('other',), 'secondary')],
                native_id=clarify_id, ttl=clarify.get_clarify_timeout(), binding=binding,
                native_valid=lambda: clarify.get_pending(clarify_id) is entry and
                    (entry.question, tuple(entry.choices or []), entry.multi_select) == snapshot)
            if not result.success:
                # Native fallback can capture typed text, even if the card send was uncertain.
                if clarify.get_pending(clarify_id) is entry:
                    clarify.mark_awaiting_text(clarify_id)
            return result
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    async def retire_clarify_card(self, clarify_id: str, notice: str) -> None:
        for card in list(self._controls().values()):
            if card.kind == 'clarify' and card.native_id == clarify_id and not card.claimed:
                await self._retire(card, notice)

    async def send_slash_confirm(self, chat_id: str, title: str, message: str, session_key: str,
                                 confirm_id: str, metadata: dict[str, Any] | None = None) -> SendResult:
        from tools import slash_confirm
        entry = slash_confirm.get_pending(session_key)
        if entry is None or entry.get('confirm_id') != confirm_id:
            return SendResult(success=False, error='Slash confirmation no longer pending')
        def pending():
            return slash_confirm.get_pending(session_key) == entry and time.time() - entry['created_at'] < slash_confirm.DEFAULT_TIMEOUT_SECONDS
        async def selected(card, value):
            if not pending():
                await self._expired_notice(chat_id)
                return
            result = await slash_confirm.resolve(session_key, confirm_id, value)
            if self._control_current(card):
                await self._control_notice(chat_id, result if isinstance(result, str) and result else 'Confirmation handled.' )
        return await self._publish(chat_id, session_key, 'slash', (confirm_id, entry['created_at']),
            f'{title}\n\n{message}', [], selected,
            buttons=[('Approve', 'once', 'primary'), ('Always approve', 'always', 'secondary'), ('Cancel', 'cancel', 'secondary')],
            native_id=confirm_id, ttl=max(0.001, slash_confirm.DEFAULT_TIMEOUT_SECONDS - (time.time() - entry['created_at'])),
            native_valid=pending)

    async def _send_exec_approval_prompt(self, prompt: ExecApprovalPrompt) -> SendResult:
        from tools import approval
        from gateway.platforms.base_exec_approval import approval_timeout_seconds
        try:
            _text(prompt.command, 4000)
            wanted = (prompt.metadata or {}).get('request_id')
            matches = [row for row in approval.list_gateway_approvals(prompt.session_key)
                       if row.get('command') == prompt.command and (wanted is None or row.get('request_id') == wanted)]
            if len(matches) != 1 or not isinstance(matches[0].get('request_id'), str) or not matches[0]['request_id']:
                raise ValueError('Cannot bind approval to one exact native request; use text fallback')
            snapshot = deepcopy(matches[0])
            request_id = snapshot['request_id']
            def pending():
                rows = [r for r in approval.list_gateway_approvals(prompt.session_key) if r.get('request_id') == request_id]
                return len(rows) == 1 and rows[0] == snapshot
            # BasePlatformAdapter.send_exec_approval normalized the allowed actions.
            # Intersect, never expand, with the native pending request's restrictions.
            allowed = {choice for _, choice, _ in prompt.actions} & {'once', 'session', 'always', 'deny'}
            smart = prompt.smart_denied or snapshot.get('smart_denied', False)
            if smart or snapshot.get('allow_session', True) is False:
                allowed -= {'session', 'always'}
            if snapshot.get('allow_permanent', True) is False or 'session' not in allowed:
                allowed.discard('always')
            labels = {'once': 'Approve', 'session': 'Approve for session', 'always': 'Always approve', 'deny': 'Deny'}
            buttons = [(labels[value], value, 'primary' if value == 'once' else 'danger' if value == 'deny' else 'secondary')
                       for value in ('once', 'session', 'always', 'deny') if value in allowed]
            async def selected(card, value):
                if value not in allowed or not pending():
                    await self._expired_notice(prompt.chat_id)
                    return
                # request_id is MANDATORY: never let an old card approve the next FIFO waiter.
                count = approval.resolve_gateway_approval(prompt.session_key, value, request_id=request_id)
                if count == 1:
                    await self._control_notice(prompt.chat_id, 'Decision submitted: ' + labels[value] + '. Execution outcome is not yet confirmed.')
                else:
                    await self._expired_notice(prompt.chat_id)
            text = 'Approve this exact action?\n\n' + prompt.description
            if smart:
                text += '\nThe safety reviewer denied it; only a one-time override is available.'
            text += '\nNo response means this request will not be approved.'
            # prompt.text may contain the core's truncated fenced preview: do NOT use it.
            # Core TurnRunner owns register_gateway_settle after delivery; do not overwrite it.
            return await self._publish(prompt.chat_id, prompt.session_key, 'approval', request_id, text, [], selected,
                buttons=buttons, preview=prompt.command, ttl=approval_timeout_seconds(), native_id=request_id,
                native_valid=pending)
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    async def send_model_picker(self, chat_id: str, providers: list, current_model: str, current_provider: str,
                                session_key: str, on_model_selected,
                                metadata: dict[str, Any] | None = None) -> SendResult:
        try:
            initial_binding = self._binding(chat_id, session_key)
            catalog = []
            for provider in providers:
                if (not isinstance(provider['slug'], str) or not provider['slug']
                        or not isinstance(provider.get('models', []), list)
                        or not all(isinstance(model, str) and model for model in provider.get('models', []))):
                    raise ValueError('Expected native provider rows with string model IDs')
                catalog.append(dict(slug=provider['slug'], name=str(provider.get('name') or provider['slug']),
                                    models=list(provider.get('models', [])), api_url=provider.get('api_url')))
            if not catalog:
                raise ValueError('No providers')
        except Exception as exc:
            return SendResult(success=False, error=str(exc))
        root_intent = (id(on_model_selected), repr(catalog), current_model, current_provider)

        async def switch(card, model, provider):
            if not self._control_current(card):
                return
            result = await on_model_selected(chat_id, model, provider['slug'])
            if self._control_current(card):
                await self._control_notice(chat_id, result if isinstance(result, str) and result else 'Model selection applied.')

        async def selected(card, value):
            action = value[0]
            if action == 'cancel':
                await self._control_notice(chat_id, 'Model selection cancelled.')
            elif action == 'providers':
                await show(None, value[1], card.binding)
            elif action == 'models':
                await show(value[1], value[2], card.binding)
            elif action == 'continue':
                await switch(card, value[1], catalog[value[2]])
            elif action == 'model':
                provider, model = catalog[value[1]], value[2]
                try:
                    from hermes_cli.model_selection_guards import combined_selection_warning
                    kwargs = {'provider': provider['slug']}
                    if provider['api_url']:
                        kwargs['base_url'] = provider['api_url']
                    warning = await asyncio.to_thread(combined_selection_warning, model, **kwargs)
                except Exception:
                    if self._control_current(card):
                        await self._control_notice(chat_id, 'Could not check model selection warnings. No model change was made; retry /model later.')
                    return
                if not self._control_current(card):
                    return
                if warning is None:
                    await switch(card, model, provider)
                else:
                    result = await self._publish(chat_id, session_key, 'model', (root_intent, 'warning', value[1], model),
                        f'{warning.title}\n\n{warning.message}', [], selected,
                        buttons=[('Continue', ('continue', model, value[1]), 'primary'), ('Cancel', ('cancel',), 'secondary')],
                        binding=card.binding)
                    if not result.success and self._control_current(card):
                        await self._control_notice(chat_id, 'Could not display the model warning. No model change was made.')

        async def show(provider_index, page, binding):
            items = catalog if provider_index is None else catalog[provider_index]['models']
            start = page * 64
            rows = []
            for index, item in enumerate(items[start:start + 64], start):
                if provider_index is None:
                    label, value = item['name'], ('models', index, 0)
                    if item['slug'] == current_provider:
                        label = '✓ ' + label
                else:
                    label, value = item, ('model', provider_index, item)
                    if item == current_model and catalog[provider_index]['slug'] == current_provider:
                        label = '✓ ' + label
                rows.append((label, value, 'secondary'))
            buttons = [('Cancel', ('cancel',), 'secondary')]
            def page_value(target):
                return ('providers', target) if provider_index is None else ('models', provider_index, target)
            if page:
                buttons.append(('Previous', page_value(page - 1), 'secondary'))
            if start + 64 < len(items):
                buttons.append(('Next', page_value(page + 1), 'secondary'))
            if provider_index is not None:
                buttons.append(('Providers', ('providers', 0), 'secondary'))
            title = 'Choose a provider' if provider_index is None else 'Choose a model: ' + catalog[provider_index]['name']
            title += f' (page {page + 1})' if len(items) > 64 else ''
            result = await self._publish(chat_id, session_key, 'model', (root_intent, provider_index, page),
                title, rows, selected, buttons=buttons, binding=binding)
            if not result.success and binding == self._binding(chat_id, session_key):
                await self._control_notice(chat_id, 'Could not display the model picker. Use /model <model-id> or retry the retained control operation.')
            return result
        return await show(None, 0, initial_binding)
