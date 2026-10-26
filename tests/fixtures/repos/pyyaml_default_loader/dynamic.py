"""Calls whose arguments are not known statically: no default-argument equivalence applies."""

import yaml


def load_all(path, *args, **options):
    with open(path) as handle:
        spread = yaml.load(handle, *args)
        keyed = yaml.load(handle, **options)
    return spread, keyed
