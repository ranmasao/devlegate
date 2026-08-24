import pytest

import nanoyaml


def test_n0_round_trip_and_nested_values():
    value = {"items": ["one", 2, {"nested": "value"}], "text": "unicode \u2603"}
    encoded = nanoyaml.dumps(value)

    assert nanoyaml.loads(encoded) == value
    assert nanoyaml.dumps(nanoyaml.loads(encoded)) == encoded


@pytest.mark.parametrize(
    "text",
    [
        "",
        "- 1\n",
        '"key": true\n',
        '"key": 1.0\n',
        '"key": []\n',
        '"key": plain\n',
        '"key": "bad\\q"\n',
        '"key": 1\n"key": 2\n',
        '---\n"key": 1\n',
    ],
)
def test_n0_rejects_broader_yaml_and_invalid_values(text):
    with pytest.raises(nanoyaml.NanoYAMLError):
        nanoyaml.loads(text)


def test_n0_rejects_empty_and_unsupported_values():
    with pytest.raises(nanoyaml.NanoYAMLError):
        nanoyaml.dumps({"items": []})
    with pytest.raises(nanoyaml.NanoYAMLError):
        nanoyaml.dumps({"enabled": True})
