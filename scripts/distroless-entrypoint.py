import os
import subprocess
import sys


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    run([sys.executable, "-m", "app.db.migrate", "upgrade"])
    os.environ["CODEX_LB_DATABASE_MIGRATE_ON_STARTUP"] = "false"
    bind_host = os.environ.get("CODEX_LB_BIND_HOST", "127.0.0.1")
    os.execvp("fastapi", ["fastapi", "run", "app/main.py", "--host", bind_host, "--port", "2455"])
