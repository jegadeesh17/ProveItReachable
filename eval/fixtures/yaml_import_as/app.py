"""Reads a settings string through an aliased import of PyYAML."""

import yaml as y


def load_config(text):
    return y.full_load(text)


if __name__ == "__main__":
    print(load_config("retries: 3"))
