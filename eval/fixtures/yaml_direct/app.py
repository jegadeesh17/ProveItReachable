"""Reads a settings string with PyYAML's FullLoader entry point."""

import yaml


def load_config(text):
    return yaml.full_load(text)


if __name__ == "__main__":
    print(load_config("retries: 3"))
