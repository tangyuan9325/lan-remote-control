# -*- coding: utf-8 -*-
"""Allow `python -m rc14` as a shortcut for the GUI/headless entry point."""

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
