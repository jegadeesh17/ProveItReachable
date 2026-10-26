"""Imports urllib3 exceptions only, never calls vulnerable URL parsing functions."""

from urllib3.exceptions import HTTPError


def check_error(exc):
    return isinstance(exc, HTTPError)


if __name__ == "__main__":
    print(check_error(ValueError()))
