"""The entry function reaches the vulnerable call whatever it is given."""

import yaml


def handle(payload):
    return yaml.unsafe_load(payload)
