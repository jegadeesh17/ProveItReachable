from shipper.parser import parse_document


def read_settings(path):
    with open(path, encoding="utf-8") as handle:
        return parse_document(handle.read())
