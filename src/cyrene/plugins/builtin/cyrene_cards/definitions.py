"""Public assistant-only card tools and bounded, data-only descriptions."""

from copy import deepcopy

ID = {"type": "string", "minLength": 1, "maxLength": 160}
TEXT = {"type": "string", "maxLength": 20000}
REVISION = {"type": "integer", "minimum": 1}


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def array(items, maximum=100):
    return {"type": "array", "items": items, "maxItems": maximum}


# The same schema describes model arguments and validates persisted input contracts.
INPUT_ITEM = obj({"type": {"enum": ["string", "number", "integer", "boolean"]},
                  "enum": {"type": "array", "minItems": 1, "uniqueItems": True}}, ["type"])
INPUT_FIELD = obj({
    "type": {"enum": ["string", "number", "integer", "boolean", "array"]},
    "title": TEXT, "description": TEXT,
    "enum": {"type": "array", "minItems": 1, "uniqueItems": True},
    "minimum": {"type": "number"}, "maximum": {"type": "number"},
    "minLength": {"type": "integer", "minimum": 0}, "maxLength": {"type": "integer", "minimum": 0},
    "format": {"enum": ["date", "date-time", "email"]},
    "minItems": {"type": "integer", "minimum": 0}, "maxItems": {"type": "integer", "minimum": 0},
    "uniqueItems": {"type": "boolean"}, "items": INPUT_ITEM,
}, ["type"])
INPUT_FIELD["oneOf"] = [
    {"properties": {"type": {"const": "array"}}, "required": ["items"]},
    {"properties": {"type": {"enum": ["string", "number", "integer", "boolean"]}},
     "not": {"required": ["items"]}},
]
INPUT_SCHEMA = obj({
    "type": {"const": "object"}, "additionalProperties": {"const": False},
    "properties": {"type": "object", "maxProperties": 64, "propertyNames": ID,
                   "additionalProperties": INPUT_FIELD},
    "required": {**array(ID, 64), "uniqueItems": True},
}, ["type", "additionalProperties"])
INPUT_SCHEMA["description"] = (
    'Flat input contract under spec.input_schema (changes.input_schema for updates). '
    'Always set type="object" and additionalProperties=false. Define each field in properties; '
    'required may only name declared fields. Field names may contain underscores. '
    'Use title/description for annotations; nested objects, $ref and pattern are unsupported. '
    'Example: {"type":"object","additionalProperties":false,"properties":'
    '{"meal_amount":{"type":"string","title":"Meal amount"}}}.'
)


# Templates are starting points, not limits on the blocks or domain of a card.
TEMPLATES = {
    "content": "text", "collection": "list", "data": "table", "input": "form", "map": "map", "custom": "embed",
}

# Read/update compatibility for persisted cards, intentionally absent from create's enum.
LEGACY_TEMPLATES = {
    "summary": "content", "recommendations": "collection", "artifacts": "collection",
    "checklist": "collection", "schedule": "collection", "activity": "collection",
    "comparison": "data", "metrics": "data", "trends": "data", "approval": "input",
    "preferences": "input", "message": "content", "entity": "content", "places": "collection",
    "context": "content", "health_overview": "data", "habit": "collection", "medication": "collection",
    "health_report": "collection",
}

FIELD = obj({"field_id": ID, "label": TEXT, "type": {"enum": ["string", "number", "integer", "boolean", "date", "select"]},
             "default": {}, "options": array(obj({"value": {}, "label": TEXT}, ["value", "label"])), "unit": TEXT}, ["field_id", "label", "type"])
ITEM = obj({"item_id": ID, "label": TEXT, "detail": TEXT, "field_id": ID, "owner": TEXT, "due_at": TEXT}, ["item_id", "label"])
COLUMN = obj({"key": ID, "label": TEXT, "format": {"enum": ["text", "number", "currency", "date"]}, "currency": TEXT}, ["key", "label"])
BLOCK_PROPERTIES = {
    "weather": {"icon": {"enum": ["clear", "partly_cloudy", "cloudy", "rain", "snow", "storm", "fog", "night"]}, "location": TEXT, "condition": TEXT, "temperature": TEXT,
                "range": TEXT, "advice": TEXT, "source": TEXT, "updated_at": TEXT},
    "action_list": {"items": array(obj({"item_id": ID, "label": TEXT, "detail": TEXT,
                    "action_ids": array(ID, 4)}, ["item_id", "label", "action_ids"])),
                    "collapsed": {"type": "boolean"}},
    "text": {"text": TEXT},
    "metrics": {"items": array(obj({"label": TEXT, "value": {"type": ["string", "number", "null"]}, "unit": TEXT, "source": TEXT}, ["label", "value"]))},
    "fields": {"items": array(obj({"label": TEXT, "value": TEXT}, ["label", "value"]))},
    "list": {"items": array(ITEM)},
    "table": {"columns": array(COLUMN, 20), "rows": array({"type": "object", "properties": {"row_id": ID}, "required": ["row_id"]}),
              "groups": array(obj({"group_id": ID, "label": TEXT}, ["group_id", "label"]), 8),
            "selection": obj({"mode": {"enum": ["single", "multiple"]}, "field_id": ID}, ["mode", "field_id"])},
    "form": {"fields": array(FIELD, 64)},
    "timeline": {"items": array(obj({"item_id": ID, "label": TEXT, "time": TEXT, "status": TEXT}, ["item_id", "label"]))},
    "files": {"items": array(obj({"artifact_id": ID, "name": TEXT, "media_type": TEXT}, ["artifact_id", "name"]))},
    "chart": {"chart_type": {"enum": ["bar", "line", "pie"]}, "labels": array(TEXT), "series": array(obj({"label": TEXT, "values": array({"type": "number"})}, ["label", "values"]), 10)},
    "map": {"places": array(obj({"place_id": ID, "label": TEXT, "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                                  "longitude": {"type": "number", "minimum": -180, "maximum": 180}, "note": TEXT, "group_id": ID}, ["place_id", "label", "latitude", "longitude"])),
            "routes": array(obj({"route_id": ID, "from_place_id": ID, "to_place_id": ID, "transport": TEXT, "note": TEXT, "group_id": ID},
                                ["route_id", "from_place_id", "to_place_id"])),
            "groups": array(obj({"group_id": ID, "label": TEXT}, ["group_id", "label"]), 8),
            "selection": obj({"mode": {"enum": ["single", "multiple"]}, "field_id": ID}, ["mode", "field_id"])},
    "embed": {"html": {"type": "string", "minLength": 1},
              "html_path": {"type": "string", "minLength": 1,
                            "description": "UTF-8 HTML file to snapshot; use instead of html for an existing artifact. Relative to the assistant workspace, or an absolute path in an owned task workspace. No HTML character limit."}},
}
BLOCK = {"oneOf": [{**obj({"block_id": ID, "type": {"const": kind, **({"default": "embed", "description": "Omitted block type defaults to embed; provide exactly one of html or html_path."} if kind == "embed" else {})}, "title": TEXT, "description": TEXT, **properties},
                       ["block_id", *([] if kind == "embed" else ["type"]), *[key for key in properties if key not in {"selection", "routes", "groups", "collapsed", "icon", "html", "html_path"}]]),
                    **({"if": {"required": ["html_path"]}, "then": {"not": {"required": ["html"]}},
                        "else": {"required": ["html"]}} if kind == "embed" else {})}
                   for kind, properties in BLOCK_PROPERTIES.items()]}
ACTION = obj({"action_id": ID, "label": TEXT, "kind": {"enum": ["submit", "confirm"]}, "fields": array(ID, 64), "intent": TEXT, "target_context_id": ID, "task_id": ID},
             ["action_id", "label", "kind", "fields", "intent"])
SPEC_PROPERTIES = {
    "home_visible": {"type": "boolean", "description": "Whether this prepared welcome slot is useful to display now. False hides it from conversation without archiving or recreating the slot."},
    "home_kind": {"enum": ["overview", "resume", "results"], "description": "Optional assistant welcome slot. One active card per kind, editable across contexts. Only create useful slots; set home_visible=false for irrelevant slots. Home sections cannot be pinned, including by the user. Always reuse the existing slot ID. Use card.list(scope=home) before creating; update existing slot."},
    "schema_version": {"const": 1}, "title": {"type": "string", "minLength": 1, "maxLength": 200, "description": "Agent-authored headline. For home overview, write the greeting here; do not use a generic card title. Line breaks are supported."},
    "subtitle": TEXT, "density": {"enum": ["compact", "comfortable"]},
    "layout": {"type": "string", "enum": ["stack", "grid"], "description": "Presentation hint: vertical stack or responsive grid; block order is preserved."},
    "blocks": {**array(BLOCK, 32), "minItems": 1},
    "input_schema": INPUT_SCHEMA, "actions": array(ACTION, 64),
}
SPEC = obj(SPEC_PROPERTIES, ["title"])
CHANGES = {**obj({k: v for k, v in SPEC_PROPERTIES.items() if k != "schema_version"}), "minProperties": 1}

DEFINITIONS = {
    "card.create": ("Create a persistent card using content, collection, data, input, map or custom as a starting point. "
                    "Welcome cards use spec.home_kind overview/resume/results with native content/collection templates. Weather block holds verified weather and provenance; action_list has collapsible rows with action_ids referencing spec.actions. Counts derive from rows. Per-action target_context_id/task_id identifies the original task to continue. Never invent weather, completion, files or approvals. "
                    "All native templates can freely mix text, metrics, fields, lists, tables, forms, timelines, files, charts and maps. "
                    "Choose block titles/order, stack or grid layout, actions and density; optionally pin atomically. "
                    "Custom requires exactly one embed block with either html or html_path, never both, and may include native blocks around it. "
                    "For an existing HTML file, pass html_path directly; the host reads and saves a snapshot, with no HTML character or card byte limit. "
                    "Omitted block type defaults to embed; native blocks must specify type. "
                    "Place input_schema and actions inside spec, never at the tool argument root. "
                    "Action fields and data-card-field bindings must name properties in spec.input_schema. "
                    "Generate the requested card directly from this contract; do not create disposable probe cards. "
                    "Successful creation emits an inline conversation card. Use pinned=true when the user requests persistent visibility in the scrollable right-hand card panel; otherwise default to false. "
                    "Custom HTML runs in an isolated iframe without network access. Use window.cyreneCard.change(values), "
                    "window.cyreneCard.submit(action_id, values), or window.cyreneCard.confirm(action_id, values) to send inputs. "
                    "Bind every iframe input/select/textarea using data-card-field='field_id' matching input_schema; the host restores saved values after reload. Custom widgets must handle cyrene-card-state event.detail.values. Declared input_schema and actions are required for iframe interactions. Selection fields must accept the exact row_id/place_id values, not display labels. Defaults are not user choices. For map category colors, declare groups (group_id, label) and assign group_id to places/routes; the UI assigns colors and shows a legend. Without groups all places share one color. Routes are straight relationship connectors, never road geometry or navigation tracks.",
                    obj({"template": {"enum": list(TEMPLATES), "description": "content: explanations and results; collection: items and progress; data: comparison and metrics; input: preferences and decisions; map: places, routes and spatial selection; custom: iframe plus optional native blocks."},
                         "spec": SPEC, "pinned": {"type": "boolean", "default": False}}, ["template", "spec"])),
    "card.update": ("Modify an existing card at expected_revision. Only supplied description fields change; arrays and objects are replaced whole. "
                    "Embed blocks require exactly one of html or html_path; use html_path to snapshot an existing UTF-8 file without copying its contents. No HTML character or card byte limit. "
                    "Omitted block type defaults to embed; native blocks must specify type. "
                    "Preserves user input and pinning. Never creates a missing card or grants user approval.",
                    obj({"card_id": ID, "expected_revision": REVISION, "changes": CHANGES, "in_response_to_event_id": ID}, ["card_id", "expected_revision", "changes"])),
    "card.get": ("Read a card's current description, origin, user drafts, submitted snapshot and placement. Optionally read paginated user events; reads do not acknowledge events.",
                 obj({"card_id": ID, "include_events": {"type": "boolean"}, "after_event_id": ID, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ["card_id"])),
    "card.list": ("List current-context, pinned, or home welcome cards. Returns summaries; use card.get for content and user inputs.",
                  obj({"scope": {"enum": ["current_task", "pinned", "home"]}, "status": {"enum": ["active", "archived"]}, "cursor": ID,
                       "limit": {"type": "integer", "minimum": 1, "maximum": 100}})),
    "card.pin": ("Pin an existing card with pinned=true or unpin with false. Keeps its conversation reference and source context. Respects a user's explicit unpin.",
                 obj({"card_id": ID, "pinned": {"type": "boolean"}, "expected_placement_revision": REVISION}, ["card_id", "pinned", "expected_placement_revision"])),
    "card.archive": ("Archive an existing card as read-only, preserving final content, inputs and pinning. Archived cards remain visible in conversation; this is not deletion or temporary-card cleanup. A completed model turn alone does not end a card.",
                     obj({"card_id": ID, "expected_revision": REVISION, "reason": TEXT}, ["card_id", "expected_revision"])),
}
TOOL_NAMES = tuple(DEFINITIONS)


def definition(name):
    description, schema = DEFINITIONS[name]
    return description, deepcopy(schema)
