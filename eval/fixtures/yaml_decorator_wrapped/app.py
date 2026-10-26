"""Decorator-wrapped function invoking vulnerable call."""

import functools
import yaml


def timed(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


@timed
def parse_payload(payload):
    return yaml.full_load(payload)


if __name__ == "__main__":
    parse_payload("active: true")
