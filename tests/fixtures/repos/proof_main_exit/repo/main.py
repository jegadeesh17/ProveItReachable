"""The script exits before it reaches the call."""

import sys

import yaml

if __name__ == "__main__":
    sys.exit(0)
    with open(__file__) as stream:
        config = yaml.load(stream)
