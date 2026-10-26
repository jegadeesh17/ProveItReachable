"""Shaped like PyRL: a safe load, then yaml.load without a Loader, in the main block; then it loops."""

import yaml

if __name__ == "__main__":
    try:
        with open(__file__) as safe_stream:
            safe = yaml.load(safe_stream, Loader=yaml.SafeLoader)
        with open(__file__) as stream:
            config = yaml.load(stream)
    except OSError as exc:
        raise SystemExit(str(exc)) from exc
    while True:
        pass
