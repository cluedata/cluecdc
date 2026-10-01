"""Insert customer, order and payment fixtures in the source database."""

import sys

from generate import main

if __name__ == "__main__":
    sys.exit(main(["insert", *sys.argv[1:]]))
