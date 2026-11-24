"""Entry point: ``python -m memebot`` or the packaged MemeBot.exe."""
from __future__ import annotations

import threading
import webbrowser

from .config import app_dir, load_config
from .engine import Engine
from .server import make_server

BANNER = r"""
  MemeBot Terminal  ·  Solana fresh-meme scanner  ·  PAPER MODE (no real money moves)
"""


def main() -> None:
    home = app_dir()
    cfg = load_config(home / "config.json")
    cfg["mode"] = "paper"
    print(BANNER)
    engine = Engine(cfg, home)
    port = int(cfg["dashboard_port"])
    for _ in range(10):
        try:
            srv = make_server(engine, port)
            break
        except OSError:
            port += 1  # port busy (another copy running?) -> try the next one
    else:
        raise SystemExit("no free port for the dashboard")
    url = f"http://127.0.0.1:{port}/"
    print(f"  data folder : {home}")
    print(f"  dashboard   : {url}")
    print("  close this window to stop the bot.\n")
    engine.start()
    if cfg.get("open_browser", True):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop.set()
        engine.save()


if __name__ == "__main__":
    main()
