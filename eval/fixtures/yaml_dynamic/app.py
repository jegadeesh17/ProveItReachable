"""Chooses the PyYAML loader function by name at run time."""

import os

import yaml


def load_config(text):
    loader = getattr(yaml, os.environ.get("YAML_LOADER", "safe_load"))
    return loader(text)


if __name__ == "__main__":
    print(load_config("retries: 3"))
