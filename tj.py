#!/usr/bin/env python3
import sys
from pathlib import Path

# Add the package directory to the Python path
# This allows us to run the script from anywhere and have it find the 'tj' package.
package_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(package_dir))

from tj.cli import entrypoint

if __name__ == "__main__":
    entrypoint()
