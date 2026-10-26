"""Direct HTTP request using urllib3 PoolManager."""

import urllib3


def fetch_url(target):
    http = urllib3.PoolManager()
    return http.request("GET", target)


if __name__ == "__main__":
    fetch_url("http://example.com")
