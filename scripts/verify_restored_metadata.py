"""Run restored-metadata verification from a source checkout."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "apps/api"))

from app.verify_restore import main  # noqa: I001


if __name__ == "__main__":
    main()
