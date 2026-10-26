"""Class method invoking vulnerable call."""

import yaml


class ConfigLoader:
    def __init__(self, raw: str):
        self.raw = raw

    def load(self):
        return yaml.full_load(self.raw)


def run():
    loader = ConfigLoader("key: val")
    return loader.load()


if __name__ == "__main__":
    run()
