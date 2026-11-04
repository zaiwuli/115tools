"""Persistent versioned components. Never overwrite a running executable."""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import httpx
from filelock import FileLock, Timeout


def root(config):
    path = config.data_dir / "components"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state(config):
    path = root(config) / "state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save(config, value):
    path = root(config) / "state.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def active_path(config, component):
    item = state(config).get(component, {}).get("current")
    return item["path"] if item else None


def inventory(config):
    data = state(config)
    return {"components": data, "p115_restart_required":
            active_path(config, "p115") != os.environ.get("WORK115_RUNNING_P115_PATH"),
            "retention_days": 7}


def run(command, timeout=600, env=None):
    result = subprocess.run(command, capture_output=True, timeout=timeout, env=env)
    if result.returncode:
        raise ValueError("COMPONENT_VALIDATION_OR_INSTALL_FAILED")
    return result


def install(config, component, version):
    if component not in {"7zip", "p115"}:
        raise ValueError("UNKNOWN_COMPONENT")
    pattern = r"\d{2}\.\d{2}" if component == "7zip" else r"[0-9a-f]{40}"
    if not re.fullmatch(pattern, version):
        raise ValueError("INVALID_COMPONENT_VERSION")
    with FileLock(str(root(config) / "update.lock"), timeout=0):
        destination = root(config) / component / (version + "-" + str(time.time_ns()))
        destination.mkdir(parents=True)
        try:
            if component == "7zip":
                if sys.platform != "linux":
                    raise ValueError("ONLINE_7ZIP_REQUIRES_LINUX_CONTAINER")
                architecture = {"x86_64": "x64", "aarch64": "arm64"}.get(platform.machine())
                if not architecture:
                    raise ValueError("UNSUPPORTED_COMPONENT_PLATFORM")
                url = f"https://github.com/ip7z/7zip/releases/download/{version}/7z{version.replace('.', '')}-linux-{architecture}.tar.xz"
                with httpx.Client(timeout=60, follow_redirects=True, max_redirects=5) as client:
                    with client.stream("GET", url) as response:
                        response.raise_for_status()
                        package = destination / "package.tar.xz"
                        size = 0
                        with package.open("wb") as output:
                            for chunk in response.iter_bytes():
                                size += len(chunk)
                                if size > 32 * 1024**2:
                                    raise ValueError("COMPONENT_DOWNLOAD_LIMIT")
                                output.write(chunk)
                digest = hashlib.sha256(package.read_bytes()).hexdigest()
                with tarfile.open(package) as archive:
                    for name in ("7zzs", "License.txt"):
                        entry = archive.getmember(name)
                        if not entry.isfile() or entry.size > 32 * 1024**2:
                            raise ValueError("COMPONENT_PACKAGE_REJECTED")
                        with archive.extractfile(entry) as source, (destination / name).open("wb") as output:
                            shutil.copyfileobj(source, output)
                executable = destination / "7zzs"
                executable.chmod(0o755)
                banner = run([str(executable), "i"], 15).stdout.decode(errors="replace")
                if version not in banner:
                    raise ValueError("COMPONENT_VERSION_MISMATCH")
                with tempfile.TemporaryDirectory(dir=destination) as sample:
                    sample = Path(sample)
                    (sample / "sample.txt").write_text("115tools validation")
                    run([str(executable), "a", str(sample / "sample.7z"), str(sample / "sample.txt")], 30)
                    run([str(executable), "t", str(sample / "sample.7z")], 30)
                    extracted = run([str(executable), "x", "-so", str(sample / "sample.7z"), "sample.txt"], 30)
                    if extracted.stdout != b"115tools validation":
                        raise ValueError("COMPONENT_EXTRACTION_VALIDATION_FAILED")
                package.unlink()
                selected = str(executable.resolve())
            else:
                target = destination / "site"
                # Separate target avoids changing dependencies of the running application.
                run([sys.executable, "-m", "uv", "pip", "install", "--target", str(target),
                     f"p115client @ git+https://github.com/ChenyangGao/p115client.git@{version}"], 900)
                env = os.environ.copy()
                env["PYTHONPATH"] = str(target)
                run([sys.executable, "-c", "from p115client import P115Client; assert callable(P115Client)"], 60, env)
                selected = str(target.resolve())
                digest = None
            item = {"version": version, "path": selected, "directory": str(destination.resolve()),
                    "installed_at": time.time(), "sha256": digest,
                    "validation": "local smoke test; upstream signature not verified"}
            data = state(config)
            record = data.setdefault(component, {})
            old = record.get("current")
            if old:
                record.setdefault("older", []).append(old)
            record["previous"] = old
            record["current"] = item
            save(config, data)
            return inventory(config)
        except (httpx.HTTPError, subprocess.SubprocessError, OSError, tarfile.TarError) as exc:
            shutil.rmtree(destination)
            raise ValueError("COMPONENT_DOWNLOAD_OR_INSTALL_FAILED") from exc
        except BaseException:
            shutil.rmtree(destination)
            raise


def rollback(config, component):
    if component not in {"7zip", "p115"}:
        raise ValueError("UNKNOWN_COMPONENT")
    with FileLock(str(root(config) / "update.lock"), timeout=0):
        data = state(config)
        record = data.get(component, {})
        current, previous = record.get("current"), record.get("previous")
        # First update rolls back to the image-bundled component.
        record["current"], record["previous"] = previous, current
        data[component] = record
        save(config, data)
        return inventory(config)


def cleanup(config, automatic=False):
    with FileLock(str(root(config) / "update.lock"), timeout=0):
        data = state(config)
        removed = 0
        for name in ("7zip", "p115"):
            record = data.get(name, {})
            protected = {item["directory"] for key in ("current", "previous")
                         if (item := record.get(key))}
            for directory in (root(config) / name).glob("*"):
                if not directory.is_dir() or str(directory.resolve()) in protected:
                    continue
                # Conservatively keep all versions for 7 days, including recently running ones.
                if time.time() - directory.stat().st_mtime < 7 * 86400:
                    continue
                running = os.environ.get("WORK115_RUNNING_P115_PATH", "")
                if running and Path(running).is_relative_to(directory):
                    continue
                lease = FileLock(str(directory) + ".lease")
                try:
                    with lease.acquire(timeout=0):
                        shutil.rmtree(directory)
                except Timeout:
                    continue
                removed += 1
        return {"removed": removed, "retention_days": 7}
