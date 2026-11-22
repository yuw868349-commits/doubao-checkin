#!/usr/bin/env python3
"""兼容入口：python checkin.py run"""

import sys

from doubao_checkin.cli import main

if __name__ == "__main__":
    sys.exit(main())
