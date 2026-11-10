"""BotSesh standalone plugin. Registration has no network/process side effects."""

def register(ctx):
    from .cli import register_cli
    register_cli(ctx)
    from .tools import register_tools
    register_tools(ctx)
    from .adapter import BotSeshAdapter, HINT, standalone_send, processed_turn, ended_turn
    from functools import partial
    ctx.register_hook('post_llm_call', processed_turn)
    ctx.register_hook('pre_llm_call', processed_turn)
    ctx.register_hook('on_session_end', ended_turn)
    from .tool_policy import enforce_tools
    ctx.register_hook('pre_tool_call', enforce_tools)
    ctx.register_platform(
        name='botsesh', label='BotSesh', adapter_factory=lambda cfg: BotSeshAdapter(cfg, poll_seconds=ctx.get_config('poll_seconds', 15)),
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
    )
