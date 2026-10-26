"""Dynamic module import via importlib."""

import importlib


def dynamic_loader(module_name, func_name, payload):
    mod = importlib.import_module(module_name)
    func = getattr(mod, func_name)
    return func(payload)


if __name__ == "__main__":
    dynamic_loader("yaml", "full_load", "key: 1")
