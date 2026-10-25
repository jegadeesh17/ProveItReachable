"""Fetches a page through a requests Session whose first request skips certificate checks."""

import requests


def fetch(url):
    session = requests.Session()
    return session.get(url, verify=False).text


if __name__ == "__main__":
    print(fetch("https://example.org"))
