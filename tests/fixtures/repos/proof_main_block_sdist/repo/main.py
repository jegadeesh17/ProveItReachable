"""Shaped like PyRL, on the version that has no Linux wheel: pyyaml==5.3.1 is built from source.

A safe load of a real YAML file, then yaml.load without a Loader (FullLoader on 5.3.1), in the main
block; then it loops. The live check (ADR-0041) proves the no-Loader call with the real PyYAML.
"""

import os

import yaml

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

if __name__ == "__main__":
    try:
        with open(CONFIG) as safe_stream:
            safe = yaml.load(safe_stream, Loader=yaml.SafeLoader)
        with open(CONFIG) as stream:
            config = yaml.load(stream)
    except OSError as exc:
        raise SystemExit(str(exc)) from exc
    while True:
        pass
