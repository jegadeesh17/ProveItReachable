"""Settings are JSON here; PyYAML is pinned but nothing imports it."""

import json


def load_config(text):
    return json.loads(text)


if __name__ == "__main__":
    print(load_config('{"retries": 3}'))
