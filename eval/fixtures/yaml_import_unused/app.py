"""Writes settings with PyYAML, but only through the safe dumper; it never loads YAML."""

import yaml


def save_config(data):
    return yaml.safe_dump(data)


if __name__ == "__main__":
    print(save_config({"retries": 3}))
