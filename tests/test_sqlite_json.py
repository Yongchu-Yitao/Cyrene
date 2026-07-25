from cyrene.runtime.sqlite_json import (
    deserialize_dict,
    deserialize_list,
    serialize_dict,
    serialize_list,
)


def test_sqlite_json_codecs_preserve_expected_container_types():
    assert deserialize_list(serialize_list(["a", 2])) == ["a", 2]
    assert deserialize_dict(serialize_dict({"a": 2})) == {"a": 2}
    assert deserialize_list('{"not": "a list"}') == []
    assert deserialize_dict('["not", "a dict"]') == {}


def test_sqlite_json_codecs_tolerate_empty_and_invalid_values():
    assert deserialize_list(None) == []
    assert deserialize_list("{invalid") == []
    assert deserialize_dict("") == {}
    assert deserialize_dict("{invalid") == {}
