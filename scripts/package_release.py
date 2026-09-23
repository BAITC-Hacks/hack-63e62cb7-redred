"""Create a source deployment archive without credentials or local data."""

from pathlib import Path, PurePosixPath
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = {"README.md", "compose.yaml", ".env.example", ".dockerignore"}
DIRECTORIES = {"backend", "frontend", "tests", "docs", "scripts", "ai", "deploy"}
SKIP = {".venv", "venv", "node_modules", "dist", "__pycache__", "uploads", "private_uploads", "reports"}


def main():
    paths = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode("utf-8").split("\0")
    destination = ROOT / "release" / "redred-server.zip"
    destination.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(paths)):
            if not name:
                continue
            path = PurePosixPath(name)
            if name not in TOP and path.parts[0] not in DIRECTORIES:
                continue
            if any(part in SKIP for part in path.parts):
                continue
            if path.name.startswith(".env") and path.name != ".env.example":
                continue
            if path.suffix in {".pyc", ".log", ".pem", ".key", ".zip"}:
                continue
            source = ROOT / name
            if source.is_file():
                archive.write(source, "redred/" + name)
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        for required in ["compose.yaml", ".env.example", "backend/bootstrap.py", "backend/Dockerfile", "frontend/Dockerfile", "frontend/nginx.conf", "frontend/package-lock.json"]:
            assert "redred/" + required in names, required
        assert not any(PurePosixPath(n).name.startswith(".env") and PurePosixPath(n).name != ".env.example" for n in names)
    print(f"{destination} ({destination.stat().st_size:,} bytes, {len(names)} files)")


if __name__ == "__main__":
    main()
