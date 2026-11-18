"""BotSesh standalone plugin. Registration has no network/process side effects; inside a running
gateway it may queue this profile's configured-but-missing channel on the gateway's own reconnect
watcher (hot start, no restart)."""

def register(ctx):
    from .cli import register_cli
    register_cli(ctx)
    from .tools import register_tools
    register_tools(ctx)
    from .adapter import BotSeshAdapter, HINT, standalone_send, processed_turn, started_turn, ended_turn
    from functools import partial
    ctx.register_hook('post_llm_call', processed_turn)
    ctx.register_hook('pre_llm_call', started_turn)
    ctx.register_hook('on_session_end', ended_turn)
    from .tool_policy import enforce_tools, restrict_request
    ctx.register_hook('pre_tool_call', enforce_tools)
    ctx.register_middleware('llm_request', restrict_request)
    # Owner-only live view (draft text + tool steps): Hermes's own observers, off the token path.
    from .live import stream_start_hook, stream_delta_hook, tool_finished_hook
    ctx.register_hook('on_stream_start', stream_start_hook)
    ctx.register_hook('on_stream_delta', stream_delta_hook)
    ctx.register_hook('post_tool_call', tool_finished_hook)
    # DM P2: cron/background and send_message deliveries go through standalone_send (durable text
    # post + exact-byte files to the owner DM). The live adapter only carries native turn finals,
    # so routing them there dropped their text as a local runtime notice.
    extra = {}
    try:
        from gateway.platforms.send_targets import StandaloneSendPolicy
        extra['standalone_send'] = StandaloneSendPolicy(label='BotSesh', all_sends=True, media=True)
    except ImportError:  # older Hermes core: keep its generic routing
        pass
    ctx.register_platform(
        name='botsesh', label='BotSesh', adapter_factory=lambda cfg: BotSeshAdapter(cfg, poll_seconds=ctx.get_config('poll_seconds', 15), guest_tools_override=ctx.get_config('guest_tools', 'server')),
        check_fn=lambda: True, validate_config=lambda config: True,
        required_env=[], install_hint='Run hermes botsesh connect. Node.js 24+ is needed only for advanced invitation access.',
        allowed_users_env='BOTSESH_ALLOWED_USERS',
        allow_all_env='BOTSESH_ALLOW_ALL_USERS',
        group_allowed_chats_env='BOTSESH_GROUP_ALLOWED_ROOMS',
        allowlist_suppresses_pairing=True,
        allow_update_command=False, max_message_length=4000,
        platform_hint=HINT, display_tier='minimal', emoji='◉',
        cron_deliver_env_var='BOTSESH_HOME_CHANNEL',
        standalone_sender_fn=partial(standalone_send, ctx=ctx),
        **extra,
    )
    from .hot_start import schedule_in_gateway
    schedule_in_gateway()
