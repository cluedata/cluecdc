"""Run bounded ecommerce INSERT and UPDATE traffic."""

import sys

from generate import main

if __name__ == "__main__":
    sys.exit(main(["run", *sys.argv[1:]]))
