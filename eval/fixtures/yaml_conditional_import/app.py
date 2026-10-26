"""Conditional import based on python version or flag."""

import sys

if sys.version_info >= (3, 8):
    import yaml
else:
    import json as yaml


def load_data(text):
    return yaml.full_load(text)


if __name__ == "__main__":
    load_data("mode: test")
