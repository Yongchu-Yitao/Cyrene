"""Validation shared by model tools and the card UI bridge."""

from copy import deepcopy
import hashlib
import json

from jsonschema import Draft202012Validator, FormatChecker

from .definitions import INPUT_SCHEMA, LEGACY_TEMPLATES, SPEC, TEMPLATES

MAX_BYTES = 256 * 1024


class CardError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def encoded(value, *, max_bytes=None):
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise CardError("invalid_card", "Card values must be finite JSON data") from exc
    if max_bytes is not None and len(result.encode()) > max_bytes:
        raise CardError("card_too_large", f"Card input exceeds {max_bytes} bytes")
    return result


def validate(schema, value):
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value), key=lambda e: str(e.path))
    if errors:
        error = errors[0]
        path = ".".join(str(item) for item in error.path)
        raise CardError("invalid_card", f"{path or 'value'}: {error.message[:400]}")


def unique(values, label):
    if len(values) != len(set(values)):
        raise CardError("invalid_card", f"Duplicate {label}")


def input_schema(schema):
    """Only bounded flat form schemas; never resolve user-authored $ref or regex."""
    try:
        validate(INPUT_SCHEMA, schema)
    except CardError as exc:
        raise CardError("invalid_input_schema", f"input_schema.{exc}") from exc
    properties = schema.get("properties", {})
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise CardError("invalid_input_schema", "Invalid input schema") from exc
    required = schema.get("required", [])
    if any(key not in properties for key in required):
        raise CardError("invalid_input_schema", "Required field is not defined")


def default_block(template):
    kind = TEMPLATES[template]
    if kind == "embed":
        raise CardError("invalid_card", "The custom template requires an embed block with HTML")
    body = {"text": ""} if kind == "text" else {"items": []}
    if kind == "table":
        body = {"columns": [], "rows": []}
    elif kind == "form":
        body = {"fields": []}
    elif kind == "chart":
        body = {"chart_type": "bar", "labels": [], "series": []}
    elif kind == "map":
        body = {"places": []}
    return {"block_id": "main", "type": kind, **body}


def _validate_action_list(block, spec):
    unique([item["item_id"] for item in block["items"]], "item_id")
    action_ids = {action["action_id"] for action in spec["actions"]}
    for item in block["items"]:
        unique(item["action_ids"], "row action_id")
        if not set(item["action_ids"]) <= action_ids:
            raise CardError("invalid_card", "List action_ids must reference declared actions")


def _validate_form(block, fields, bindings):
    for field in block["fields"]:
        key = field["field_id"]
        expected = {"select": "string", "date": "string"}.get(field["type"], field["type"])
        if fields.get(key, {}).get("type") != expected:
            raise CardError("invalid_card", f"Form field does not match input schema: {key}")
        if field["type"] == "date" and fields[key].get("format") != "date":
            raise CardError("invalid_card", "Date fields require format=date in the input schema")
        if "default" in field:
            validate(fields[key], field["default"])
        for option in field.get("options", []):
            validate(fields[key], option["value"])
        bindings.append(key)


def _validate_map(block):
    unique([p["place_id"] for p in block["places"]], "place_id")
    unique([r["route_id"] for r in block.get("routes", [])], "route_id")
    groups = block.get("groups", [])
    unique([g["group_id"] for g in groups], "group_id")
    group_ids = {g["group_id"] for g in groups}
    if any(item.get("group_id") not in group_ids for item in [*block["places"], *block.get("routes", [])] if "group_id" in item):
        raise CardError("invalid_card", "Map group_id must reference a declared group")
    ids = {p["place_id"] for p in block["places"]}
    if any(r["from_place_id"] not in ids or r["to_place_id"] not in ids for r in block.get("routes", [])):
        raise CardError("invalid_card", "Map route endpoints must reference existing places")


def _validate_table(block):
    unique([c["key"] for c in block["columns"]], "column key")
    unique([r["row_id"] for r in block["rows"]], "row_id")
    keys = {c["key"] for c in block["columns"]} | {"row_id"}
    if any(set(row) - keys for row in block["rows"]):
        raise CardError("invalid_card", "Table row contains an undeclared column")


def _validate_selection(block, fields, bindings):
    kind = block["type"]
    selection = block.get("selection")
    if selection:
        key = selection["field_id"]
        expected = "string" if selection["mode"] == "single" else "array"
        if fields.get(key, {}).get("type") != expected:
            raise CardError("invalid_card", "Selection does not match input schema")
        # A rendered option must be acceptable to its declared input.
        # Otherwise the card can be created but no click can be saved.
        item_schema = fields[key] if expected == "string" else fields[key].get("items", {})
        items = block["rows"] if kind == "table" else block["places"]
        id_key = "row_id" if kind == "table" else "place_id"
        for item in items:
            try:
                validate(item_schema, item[id_key])
            except CardError as exc:
                raise CardError("invalid_card", f"Selection {key} must accept every {id_key}; use IDs, not display labels: {item[id_key]}") from exc
        bindings.append(key)


def _validate_checklist(block, fields, bindings):
    unique([item["item_id"] for item in block["items"]], "item_id")
    for item in block["items"]:
        if "field_id" in item:
            key = item["field_id"]
            if fields.get(key, {}).get("type") != "boolean":
                raise CardError("invalid_card", "Checklist inputs must be boolean")
            bindings.append(key)


def _validate_blocks(spec, fields, bindings):
    for block in spec["blocks"]:
        kind = block["type"]
        if kind == "action_list":
            _validate_action_list(block, spec)
        if kind == "form":
            _validate_form(block, fields, bindings)
        if kind == "map":
            _validate_map(block)
        if kind == "table":
            _validate_table(block)
        if kind in {"table", "map"}:
            _validate_selection(block, fields, bindings)
        if kind in {"list", "timeline"}:
            _validate_checklist(block, fields, bindings)
        if kind == "chart" and any(len(x["values"]) != len(block["labels"]) for x in block["series"]):
            raise CardError("invalid_card", "Chart labels and values must have equal lengths")


def normalize(template, supplied, *, read_html=None):
    # Stored legacy cards keep their IDs, template identity and authored blocks.
    # New creation is restricted to the six public names by the tool schema.
    template = LEGACY_TEMPLATES.get(template, template)
    if template not in TEMPLATES:
        raise CardError("invalid_template", "Unknown card template")
    encoded(supplied)
    validate(SPEC, supplied)
    spec = {"schema_version": 1, "subtitle": "", "density": "comfortable", "layout": "stack", "actions": [],
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}, **deepcopy(supplied)}
    if "blocks" not in spec:
        spec["blocks"] = [default_block(template)]
    for block in spec["blocks"]:
        block.setdefault("type", "embed")
        if "html_path" in block:
            if read_html is None:
                raise CardError("html_file_unavailable", "HTML file access requires a workspace resolver")
            block["html"] = read_html(block.pop("html_path"))
    # Validate resolved content too (in particular, reject empty files).
    validate(SPEC, spec)
    if not spec["title"].strip():
        raise CardError("invalid_card", "Card title must not be blank")
    input_schema(spec["input_schema"])
    fields = spec["input_schema"].get("properties", {})
    unique([b["block_id"] for b in spec["blocks"]], "block_id")
    unique([a["action_id"] for a in spec["actions"]], "action_id")
    bindings = []
    _validate_blocks(spec, fields, bindings)
    unique(bindings, "input binding")
    if template == "custom" and sum(block["type"] == "embed" for block in spec["blocks"]) != 1:
        raise CardError("invalid_card", "Custom cards require exactly one embed block")
    if template != "custom" and any(b["type"] == "embed" for b in spec["blocks"]):
        raise CardError("invalid_card", "Use the custom template for iframe content")
    for action in spec["actions"]:
        unique(action["fields"], "action field")
        if not set(action["fields"]) <= fields.keys():
            raise CardError("invalid_card", "Action refers to an undefined input")
    encoded(spec)
    return spec


def validate_values(spec, values, *, complete=False):
    encoded(values, max_bytes=MAX_BYTES)
    schema = deepcopy(spec["input_schema"])
    if not complete:
        schema.pop("required", None)
    validate(schema, values)
    for block in spec["blocks"]:
        selection = block.get("selection")
        if not selection or selection["field_id"] not in values:
            continue
        selected = values[selection["field_id"]]
        selected = [selected] if selection["mode"] == "single" else selected
        allowed = {p["place_id"] for p in block["places"]} if block["type"] == "map" else {r["row_id"] for r in block["rows"]}
        if not set(selected) <= allowed:
            raise CardError("invalid_card", "Selection contains an unknown item")


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()
