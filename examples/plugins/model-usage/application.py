"""Application RPC contributed by the example PluginPack."""

from agent.plugin import PluginApplicationContext
from cyrene.runtime.database import get_token_usage_stats


def setup_application(context: PluginApplicationContext) -> None:
    async def usage_load(arguments, _request_context):
        source = arguments if isinstance(arguments, dict) else {}
        days = max(1, min(int(source.get("days") or 7), 90))
        return await get_token_usage_stats(context.db_path, days=days, model="")

    context.provide_frontend_method("usage.load", usage_load)
