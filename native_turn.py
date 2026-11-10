"""Canonical native admission is independent of model and outbound completion.

The Hermes 0.22.5 producer seam is deliberately feature-required. An older
runtime must refuse admission, not guess that a successful callback ran a model.
"""
from __future__ import annotations
import asyncio
from dataclasses import asdict

from .client import BotSeshError
from .reliability import receipt, health


class NativeTurnMixin:
    async def _admit_event(self, event):
        from gateway.platforms.webhook_ingress import admit_producer
        from agent.safe_worker_policy import safe_worker_enabled
        from hermes_cli.lifecycle import has_hook
        if safe_worker_enabled() or not has_hook('pre_tool_call'):
            raise BotSeshError('native_tool_policy_hook_required')
        admitted = await admit_producer(self, event)
        if admitted is None:
            return False
        current = self._current[event.source.chat_id]
        # Only opaque receipt identity, never a credential or mutable name.
        native = asdict(admitted)
        current['native_admission'] = native
        current['event'] = event
        receipt(self.client.store, event.source.chat_id, current['target'], native_admission=native)
        return True

    async def _native_state(self, current):
        native = current.get('native_admission')
        if not native:
            return None
        from gateway.platforms.webhook_ingress import producer_scope
        from gateway.session_results import admission_result
        from hermes_state_runtime import get_session_admission
        async with producer_scope(self, current['event']) as authority:
            if authority.profile_id != native['ref']['profile_id']:
                raise BotSeshError('native_admission_scope_mismatch')
            def read():
                row = get_session_admission(authority.db, admission_id=native['admission_id'])
                if not row or row['target_session_id'] != native['ref']['session_id']:
                    raise BotSeshError('native_admission_missing')
                result = admission_result(authority.db, native['admission_id']) if row['status'] == 'terminal' else None
                return row, result
            row, result = await asyncio.to_thread(read)
        value = (result or {}).get('result') or {}
        # Explicit execution outcome/flags, never apology text or a handler ACK.
        if row['status'] == 'terminal':
            current['native_terminal'] = row.get('outcome')
            current['model_processed'] = (row.get('outcome') == 'completed'
                and value.get('completed', True) is True
                and not value.get('failed') and not value.get('interrupted') and not value.get('error'))
        return row, value

    async def _reconcile_native(self, room_id):
        current = self._current.get(room_id)
        if not current or not current.get('native_admission'):
            return
        state = await self._native_state(current)
        row, value = state
        if row['status'] in ('queued', 'started'):
            return  # Admission/worker liveness does not attest any context.
        if row['status'] == 'unknown':
            health(self.client.store, room_id, state='execution_unknown', error='interrupted')
            return  # Side effects unknown: neither replay nor invent a terminal.
        if row['status'] != 'terminal':
            return
        # An actual output is still owed by the core delivery ledger. Its exact
        # final may contain native media-egress warnings; never synthesize it
        # from the redacted state.db result or manufacture a replacement UUID.
        if (current.get('model_processed') and value.get('final_response')
                and not current.get('delivered') and not current.get('failure_delivered')):
            if not await self._posted_reply(room_id, current['target'], current['receipt_id']):
                return
        outcome = row.get('outcome')
        code = ('cancelled' if outcome == 'cancelled' else 'interrupted' if outcome == 'interrupted'
                else 'empty_response' if current.get('model_processed') else current.get('provider_hint') or 'runtime_error')
        await self._finish_current(room_id, current, code)

    async def _finish_current(self, room_id, current, code):
        # Do not release identity until the durable final/failure is journaled.
        if await self._posted_reply(room_id, current['target'], current['receipt_id']):
            current['delivered'] = True
        room = self.client.store.room(room_id)
        if room.get('processing_receipts', {}).get(str(current['target']), {}).get('explicit_failure'):
            await self._flush_explicit_failure(room_id, current['target'])
        elif current.get('delivered'):
            self._mark_handled(room_id, current['target'])
        elif room.get('pending_completion'):
            health(self.client.store, room_id, state='completion_pending', error='retry_original_operation')
        elif not current.get('failure_delivered') and not room.get('pending_final'):
            self._queue_failure(room_id, current['target'], code)
            await self._flush_failures(room_id)
        from .task_failure import settle_takeover
        await asyncio.to_thread(settle_takeover, self.client, room_id, current['target'])
        if self._current.get(room_id) is current:
            self._current.pop(room_id)

    def _restore_native(self, room_id, row):
        """Restore only the same saved target; never rerun an uncertain model turn."""
        from gateway.platforms.event import MessageEvent, MessageType
        frozen = row.get('frozen') or {}
        messages = [m for m in frozen.get('batch', {}).get('messages', []) if m.get('number') == row['target']]
        if len(messages) != 1 or not row.get('text') or not row.get('receipt_id'):
            raise BotSeshError('native_admission_context_missing')
        message = messages[0]
        binding = self.client.store.room(room_id)
        source = self.build_source(chat_id=room_id, chat_name=binding.get('name', room_id),
            chat_type='dm' if binding.get('room_type') == 'dm' else 'group',
            user_id=message['senderParticipantId'], message_id=str(row['target']))
        event = MessageEvent(text=row['text'], source=source, message_id=str(row['target']),
            user_id=message['senderParticipantId'], message_type=MessageType.TEXT,
            raw_message=message, allow_gateway_control=False, reply_expected=True, auto_skill='botsesh:botsesh')
        current = {key: row[key] for key in ('receipt_id', 'target', 'client_id', 'text', 'native_admission')}
        current.update(event=event, delivered=False, model_processed=False, tool_policy=row.get('tool_policy'))
        self._current[room_id] = current
