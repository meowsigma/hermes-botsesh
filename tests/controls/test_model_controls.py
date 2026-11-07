import asyncio
import threading
from types import SimpleNamespace
from uuid import UUID
import pytest

from test_dm_controls import async_test, harness


def option(wire, label):
    return next(item['id'] for item in wire['buttons'] + wire['choices'] if item['label'] == label)


@async_test
async def test_model_provider_pagination_full_ids_and_explicit_warning(monkeypatch):
    from hermes_cli import model_selection_guards as guards
    a = harness()
    model_id, slug = 'model/' + 'm' * 300, 'custom/' + 'p' * 200
    providers = [{'slug': f'p{i}', 'name': f'Provider {i}', 'models': ['small']} for i in range(64)]
    providers.append({'slug': slug, 'name': 'Last provider', 'models': ['first'] * 64 + [model_id]})
    seen, checked = [], []
    loop_thread = threading.get_ident()
    def warning(model, **kwargs):
        assert threading.get_ident() != loop_thread
        checked.append((model, kwargs['provider']))
        return SimpleNamespace(title='Cost warning', message='May incur charges; continue?')
    monkeypatch.setattr(guards, 'combined_selection_warning', warning)
    async def selected(chat_id, model, provider):
        seen.append((chat_id, model, provider))
        return 'Model changed.'
    assert (await a.send_model_picker('dm', providers, 'current', 'current-provider', 'session', selected)).success
    first = a.calls[-1][2]
    assert len(first['choices']) == 64
    await a._control_receive('dm', a.choice(first, optionId=option(first, 'Next')))
    second = a.calls[-1][2]
    assert second['callbackId'] != first['callbackId']
    await a._control_receive('dm', a.choice(second, optionId=option(second, 'Last provider')))
    models = a.calls[-1][2]
    await a._control_receive('dm', a.choice(models, optionId=option(models, 'Next')))
    last_model = a.calls[-1][2]
    await a._control_receive('dm', a.choice(last_model, optionId=last_model['choices'][0]['id']))
    confirm = a.calls[-1][2]
    assert not seen and checked == [(model_id, slug)]
    assert 'May incur charges' in confirm['text']
    await a._control_receive('dm', a.choice(confirm, optionId=option(confirm, 'Continue')))
    assert seen == [('dm', model_id, slug)]
    assert a.notices[-1][1] == 'Model changed.'
    for _, _, wire in a.calls:
        if 'choices' not in wire:
            continue
        assert len(wire['choices']) <= 64 and len(wire['buttons']) <= 8
        for row in wire['choices'] + wire['buttons']:
            assert UUID(row['id']).version == 4
            assert model_id not in row['id'] and slug not in row['id']


@async_test
async def test_model_warning_error_fails_closed_and_cancel_never_switches(monkeypatch):
    from hermes_cli import model_selection_guards as guards
    a, seen = harness(), []
    async def selected(*args):
        seen.append(args)
    def broken(*args, **kwargs):
        raise RuntimeError('cannot check warning')
    monkeypatch.setattr(guards, 'combined_selection_warning', broken)
    providers = [{'slug': 'p', 'name': 'P', 'models': ['m']}]
    await a.send_model_picker('dm', providers, '', '', 'session', selected)
    await a._control_receive('dm', a.choice())
    await a._control_receive('dm', a.choice())
    assert seen == [] and any('warning' in text.lower() for _, text in a.notices)
    monkeypatch.setattr(guards, 'combined_selection_warning', lambda *a, **k: SimpleNamespace(title='Warning', message='Risk'))
    await a.send_model_picker('dm', providers, '', '', 'session', selected)
    await a._control_receive('dm', a.choice())
    await a._control_receive('dm', a.choice())
    wire = a.calls[-1][2]
    await a._control_receive('dm', a.choice(wire, optionId=option(wire, 'Cancel')))
    assert seen == []


@pytest.mark.parametrize('boundary', ['generation', 'expiry', 'authorization'])
@async_test
async def test_model_boundary_change_while_guard_runs_cannot_switch(monkeypatch, boundary):
    from hermes_cli import model_selection_guards as guards
    a, seen = harness(), []
    started, release = threading.Event(), threading.Event()
    def warning(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return None
    monkeypatch.setattr(guards, 'combined_selection_warning', warning)
    async def selected(*args):
        seen.append(args)
    await a.send_model_picker('dm', [{'slug': 'p', 'name': 'P', 'models': ['m']}], '', '', 'session', selected)
    await a._control_receive('dm', a.choice())
    task = asyncio.create_task(a._control_receive('dm', a.choice()))
    assert await asyncio.to_thread(started.wait, 3)
    if boundary == 'generation':
        a.ctx['generation'] += 1
    elif boundary == 'expiry':
        next(reversed(a._controls().values())).expires = 0
    else:
        a.authorized = False
    release.set()
    await task
    assert seen == []
