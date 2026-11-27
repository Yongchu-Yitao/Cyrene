"""Persist time snapshots on the active path, never in process-global state."""
from datetime import datetime, timezone
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from cyrene.core.context.graph_reads import latest_path_value
from cyrene.core.hook import MODEL_START
from cyrene.platform import settings_store

INTERVAL_SECONDS = 300
KIND = "current_time"
SOURCE = "cyrene_time_context"
HOOK_ID = "cyrene-time-context-model-start"
PLUGIN_ID = "cyrene_time_context.mount"


def _previous(value):
    if value is not None:
        try:
            snapshot = ElementTree.fromstring(value["content"])
            stamp = datetime.fromisoformat(snapshot.findtext("sampled_at", ""))
            if stamp.tzinfo is not None:
                return stamp, snapshot.findtext("timezone", "")
        except (ValueError, KeyError, ElementTree.ParseError):
            pass
    return None


def setup(context):
    async def mount(event):
        # Read the same setting used by Settings; never infer the server timezone.
        zone_name = settings_store.get("timezone", "Asia/Shanghai") or "Asia/Shanghai"
        zone = ZoneInfo(zone_name)
        now = event.time.astimezone(timezone.utc)
        local = now.astimezone(zone)
        node_id = (event.payload or {}).get("node_id")
        previous = _previous(latest_path_value(context.tree, context.tree_id, node_id,
                                               role="context", context_source=SOURCE))
        elapsed_text = "首次注入"
        if previous:
            stamp, previous_zone = previous
            elapsed = (now - stamp.astimezone(timezone.utc)).total_seconds()
            if (0 <= elapsed < INTERVAL_SECONDS and previous_zone == zone_name
                    and stamp.astimezone(zone).date() == local.date()):
                return {}
            if elapsed < 0:
                elapsed_text = "系统时钟回拨，已重新采样"
            else:
                minutes, seconds = divmod(int(elapsed), 60)
                elapsed_text = f"{minutes} 分 {seconds} 秒"
        snapshot = ElementTree.Element(KIND)
        ElementTree.SubElement(snapshot, "sampled_at").text = local.isoformat()
        ElementTree.SubElement(snapshot, "timezone").text = zone_name
        ElementTree.SubElement(snapshot, "elapsed_since_previous").text = elapsed_text
        ElementTree.SubElement(snapshot, "note").text = (
            "这是本次上下文更新时采样的时间；时间判断以最新快照为准。"
        )
        return {"context": ElementTree.tostring(snapshot, encoding="unicode"),
                "context_kind": KIND, "context_source": SOURCE,
                "context_lifecycle": "reference"}

    if HOOK_ID in {hook.id for hook in context.hooks.list()}:
        context.hooks.bind_plugin(PLUGIN_ID, mount, replace=True)
    else:
        context.hooks.register(MODEL_START, mount, plugin_id=PLUGIN_ID,
                               hook_id=HOOK_ID, root_only=True, failure_policy="open")
