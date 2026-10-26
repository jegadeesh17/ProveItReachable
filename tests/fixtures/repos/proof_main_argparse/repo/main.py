"""A required command-line argument is parsed before the call."""

import argparse

import yaml

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args()
    with open(args.config) as stream:
        config = yaml.load(stream)
