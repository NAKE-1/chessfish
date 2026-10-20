"""PyInstaller entry point (the package uses relative imports, so it can't be the script itself)."""
import sys

from chessfish_host.__main__ import main

sys.exit(main())
