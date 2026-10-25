import argparse

from shipper.service import read_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args()
    print(read_settings(args.path))


if __name__ == "__main__":
    main()
