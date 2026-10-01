"""Update only ecommerce fixtures created by this generator."""

import sys

from generate import main

if __name__ == "__main__":
    sys.exit(main(["update", *sys.argv[1:]]))
