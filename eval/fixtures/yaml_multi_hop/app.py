"""Multi-hop chain from entry point to vulnerable call."""

import yaml


def step_four(data):
    return yaml.full_load(data)


def step_three(data):
    return step_four(data)


def step_two(data):
    return step_three(data)


def step_one(data):
    return step_two(data)


if __name__ == "__main__":
    step_one("test: 1")
