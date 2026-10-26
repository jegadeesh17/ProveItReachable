import yaml


def test_yaml_parsing():
    parsed = yaml.full_load("name: test")
    assert parsed["name"] == "test"
