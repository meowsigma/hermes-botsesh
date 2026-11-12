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
        from hermes_cli.plugins import has_middleware
        from .tool_policy import host_tools_allowed, check_chat_only_runtime
        if not has_middleware('llm_request'):
            raise BotSeshError('native_tool_allowlist_required')
        current = self._current[event.source.chat_id]
        if not host_tools_allowed(current.get('tool_policy'), self._guest_tools_override):
            check_chat_only_runtime()
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
        from gateway.response_filters import is_intentional_silence_response
        if current.get('model_processed') and is_intentional_silence_response(value.get('final_response')):
            current['intentional_silence'] = True
        saved = self.client.store.room(room_id).get('processing_receipts', {}).get(str(current['target']), {})
        # An actual output is still owed by the core delivery ledger. Its exact
        # final may contain native media-egress warnings; never synthesize it
        # from the redacted state.db result or manufacture a replacement UUID.
        if (current.get('model_processed') and value.get('final_response')
                and not current.get('intentional_silence') and not saved.get('explicit_pass')
                and not saved.get('explicit_post_calls')
                and not current.get('delivered') and not current.get('failure_delivered')):
            if not await self._posted_reply(room_id, current['target'], current['receipt_id']):
                return
        outcome = row.get('outcome')
        code = ('cancelled' if outcome == 'cancelled' else 'interrupted' if outcome == 'interrupted'
                else 'empty_response' if current.get('model_processed') else current.get('provider_hint') or 'runtime_error')
        await self._finish_current(room_id, current, code)

    async def _finish_current(self, room_id, current, code):
        calls = self.client.store.room(room_id).get('processing_receipts', {}).get(str(current['target']), {}).get('explicit_post_calls', {})
        for client_id, call in calls.items():
            if call.get('result') or call.get('rejected') or not call.get('payload'):
                continue
            try:
                result = await asyncio.to_thread(self.client.enrolled, room_id, 'post', **call['payload'])
            except BotSeshError as error:
                if not error.definitive:
                    raise  # Preserve the exact unresolved send and frozen context.
                self.client.store.update(lambda d: d['rooms'][room_id]['processing_receipts'][str(current['target'])]['explicit_post_calls'][client_id].update(rejected={'code': error.code, 'status': error.status}))
            else:
                self.client.store.update(lambda d: d['rooms'][room_id]['processing_receipts'][str(current['target'])]['explicit_post_calls'][client_id].update(result=result))
        # Do not release identity until the durable final/failure is journaled.
        if await self._posted_reply(room_id, current['target'], current['receipt_id']):
            current['delivered'] = True
        room = self.client.store.room(room_id)
        row = room.get('processing_receipts', {}).get(str(current['target']), {})
        rejected_posts = row.get('explicit_post_calls') and all(call.get('rejected') for call in row['explicit_post_calls'].values())
        if rejected_posts and not current.get('delivered') and not row.get('read_attested'):
            self._queue_failure(room_id, current['target'], 'runtime_error')
            await self._flush_failures(room_id)
        elif row.get('explicit_pass') or (current.get('model_processed') and current.get('intentional_silence')
                                       and not row.get('explicit_post_calls')):
            from .room_pass import pass_frozen
            await asyncio.to_thread(pass_frozen, self.client, room_id, current['receipt_id'], True, target=current['target'])
            for target in current.get('batch_targets', [current['target']]):
                self._mark_handled(room_id, target, state='replied' if row.get('posted_reply') else 'passed')
        elif row.get('explicit_failure'):
            await self._flush_explicit_failure(room_id, current['target'])
        elif current.get('delivered') or row.get('read_attested'):
            # A confirmed processed:true post/ack is its own explicit read
            # attestation, even if the runtime fails after that useful answer.
            # Keep the native failure ledger, not a failed/unanswered room ask.
            await self._delivery(room_id, current['target'], 'completed', batch_id=current['receipt_id'])
            for target in current.get('batch_targets', [current['target']]):
                self._mark_handled(room_id, target, state='replied' if target == current['target'] and current.get('delivered') else 'passed')
        elif row.get('explicit_post_calls') and current.get('model_processed'):
            # An authored no-read post is not implicit read permission. Retain
            # local completion separately; no read checkpoint is advanced.
            await self._delivery(room_id, current['target'], 'completed_unread', batch_id=current['receipt_id'])
            for target in current.get('batch_targets', [current['target']]):
                receipt(self.client.store, room_id, target, state='completed_unread')
        elif room.get('pending_completion'):
            health(self.client.store, room_id, state='completion_pending', error='retry_original_operation')
        elif not current.get('failure_delivered') and not room.get('pending_final'):
            self._queue_failure(room_id, current['target'], code)
            await self._flush_failures(room_id)
        from .task_failure import settle_takeover
        await asyncio.to_thread(settle_takeover, self.client, room_id, current['target'])
        if self._current.get(room_id) is current:
            self._current.pop(room_id)
        # Only terminal native settlement retires frozen policy context.
        def retire(data):
            # Older inline/Advanced delivery journals may predate receipts.
            # Retirement must not create an attestation or crash their replay.
            row = data['rooms'].get(room_id, {}).get('processing_receipts', {}).get(str(current['target']), {})
            row.pop('frozen', None)
            row.pop('text', None)
        self.client.store.update(retire)

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
        event = MessageEvent(text=row['text'], source=source, message_id=row.get('event_id', str(row['target'])),
            user_id=message['senderParticipantId'], message_type=MessageType.TEXT,
            raw_message=message, allow_gateway_control=False, reply_expected=True, auto_skill='botsesh:botsesh')
        current = {key: row[key] for key in ('receipt_id', 'target', 'client_id', 'text', 'native_admission')}
        current['batch_targets'] = row.get('batch_targets', [row['target']])
        current['event_id'] = row.get('event_id', str(row['target']))
        current.update(event=event, delivered=False, model_processed=False, tool_policy=row.get('tool_policy'))
        self._current[room_id] = current
