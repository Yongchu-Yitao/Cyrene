from copy import deepcopy

import pytest
from jsonschema import validators

from cyrene.core.plugin import validation


@pytest.fixture(autouse=True)
def clear_cache():
    validation._SCHEMA_CHECK_CACHE.clear()
    yield
    validation._SCHEMA_CHECK_CACHE.clear()


def track_checks(monkeypatch, validator=validators.Draft202012Validator):
    calls = []
    original = validator.check_schema

    def checked(cls, schema, *args, **kwargs):
        calls.append(deepcopy(schema))
        return original(schema, *args, **kwargs)

    monkeypatch.setattr(validator, "check_schema", classmethod(checked))
    return calls


def test_equal_schemas_reuse_success_but_mutations_revalidate(monkeypatch):
    calls = track_checks(monkeypatch)
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    validation.check_input_schema(schema)
    validation.check_input_schema(deepcopy(schema))
    assert len(calls) == 1
    schema["properties"]["name"]["type"] = "number"
    validation.check_input_schema(schema)
    assert len(calls) == 2
    schema["properties"]["name"]["type"] = "invalid-type"
    for _ in range(2):
        with pytest.raises(validation.PluginSchemaError, match="invalid Plugin input_schema"):
            validation.check_input_schema(schema)
    assert len(calls) == 4


def test_validator_and_meta_schema_changes_invalidate_success(monkeypatch):
    current = track_checks(monkeypatch)
    legacy = track_checks(monkeypatch, validators.Draft7Validator)
    schema = {"type": "object"}
    validation.check_input_schema(schema)
    meta = deepcopy(validators.Draft202012Validator.META_SCHEMA)
    meta["description"] = "Changed meta schema"
    monkeypatch.setattr(validators.Draft202012Validator, "META_SCHEMA", meta)
    validation.check_input_schema(schema)
    assert len(current) == 2
    monkeypatch.setattr(validators, "validator_for", lambda _, **kwargs: validators.Draft7Validator)
    validation.check_input_schema(schema)
    assert len(legacy) == 1


def test_cache_is_bounded_and_evicts_oldest_success(monkeypatch):
    calls = track_checks(monkeypatch)
    monkeypatch.setattr(validation, "_SCHEMA_CACHE_LIMIT", 2)
    for title in ("a", "b", "c", "a"):
        validation.check_input_schema({"type": "object", "title": title})
    assert len(calls) == 4
    assert len(validation._SCHEMA_CHECK_CACHE) == 2


def test_non_json_schemas_and_custom_validators_are_not_cached(monkeypatch):
    calls = track_checks(monkeypatch)
    schema = {"type": "object", "default": (1, 2)}
    validation.check_input_schema(schema)
    validation.check_input_schema(schema)
    assert len(calls) == 2
    custom = validators.extend(validators.Draft202012Validator)
    assert validation._schema_check_key({"type": "object"}, custom) is None


def test_large_schema_keys_are_not_retained(monkeypatch):
    calls = track_checks(monkeypatch)
    monkeypatch.setattr(validation, "_SCHEMA_CACHE_KEY_LIMIT", 10)
    for _ in range(2):
        validation.check_input_schema({"type": "object"})
    assert len(calls) == 2
    assert not validation._SCHEMA_CHECK_CACHE
