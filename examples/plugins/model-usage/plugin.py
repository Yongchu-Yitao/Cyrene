from cyrene.config import DB_PATH
from cyrene.runtime.database import get_token_usage_stats


async def usage_load(args):
    days = max(1, min(int((args or {}).get("days") or 7), 90))
    return await get_token_usage_stats(str(DB_PATH), days=days, model="")


def activate(context):
    context.register_method("usage.load", usage_load)
