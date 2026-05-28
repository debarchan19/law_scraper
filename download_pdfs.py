"""Entry point — delegates to the indiankanoon package."""
import sys
from indiankanoon.cli import main

if __name__ == "__main__":
    sys.exit(main())
