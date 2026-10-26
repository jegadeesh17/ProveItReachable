"""Vulnerable call exists in dead code block behind False condition."""

import yaml


def run():
    if False:
        return yaml.full_load("unreachable: true")
    return "ok"


if __name__ == "__main__":
    run()
