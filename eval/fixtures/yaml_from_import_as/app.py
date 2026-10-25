"""Reads a settings string through a renamed from-import of PyYAML."""

from yaml import full_load as parse_settings


def load_config(text):
    return parse_settings(text)


if __name__ == "__main__":
    print(load_config("retries: 3"))
