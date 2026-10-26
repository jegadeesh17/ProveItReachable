"""The vulnerable call sits behind a check a placeholder argument does not pass."""

import yaml


def handle(payload):
    if isinstance(payload, str):
        return yaml.unsafe_load(payload)
    return None
