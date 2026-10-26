"""Calls that name a Loader: never the default, never a use of yaml.full_load."""

import yaml


def load_settings(path):
    with open(path) as handle:
        first = yaml.load(handle, Loader=yaml.SafeLoader)
        second = yaml.load(handle, yaml.SafeLoader)
    return first, second
