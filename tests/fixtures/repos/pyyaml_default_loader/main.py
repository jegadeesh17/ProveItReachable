"""Shaped like PyRL: yaml.load without a Loader, inside the main block and in a helper."""

import sys

import yaml

import dynamic
import settings


def _get_config(path):
    with open(path) as handle:
        return yaml.load(handle)


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as stream:
            data = yaml.load(stream)
            config = _get_config(sys.argv[2])
            safe = settings.load_settings(sys.argv[3])
            extra = dynamic.load_all(sys.argv[4])
    except OSError as exc:
        raise SystemExit(str(exc)) from exc
    print(data, config, safe, extra)
