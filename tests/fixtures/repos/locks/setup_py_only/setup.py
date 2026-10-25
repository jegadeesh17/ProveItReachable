import os

from setuptools import setup

os.makedirs("/tmp/pir-fixture-setup-py-ran", exist_ok=True)  # must never run

setup(name="demo", install_requires=["requests>=2", "Flask_Login"])
