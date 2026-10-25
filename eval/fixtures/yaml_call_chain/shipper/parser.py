import yaml


def parse_document(text):
    return yaml.full_load(text)
