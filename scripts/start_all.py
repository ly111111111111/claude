"""一键启动 Claude Agent SDK 聊天服务（MiniMax 直连，无需本机桥）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    raise SystemExit(
        subprocess.call([sys.executable, "-m", "backend.main"], cwd=str(ROOT))
    )


if __name__ == "__main__":
    main()
