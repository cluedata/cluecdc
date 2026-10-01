"""Add, change defaults or drop cdc_test_ columns explicitly."""

import sys

from generate import main

if __name__ == "__main__":
    sys.exit(main(["alter", *sys.argv[1:]]))
