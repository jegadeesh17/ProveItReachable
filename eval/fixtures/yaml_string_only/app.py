"""Application code mentions yaml.full_load in a string/docstring only."""

DOCUMENTATION = "Use yaml.full_load(s) when reading YAML streams in external tools."


def log_recommendation():
    msg = "Warning: Avoid calling yaml.load or yaml.full_load directly."
    print(msg)


if __name__ == "__main__":
    log_recommendation()
