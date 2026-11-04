"""Bounded ZIP/TAR extraction in a separate, cancelable process."""
import argparse
import hashlib
import json
import os
import contextlib
import shutil
import stat
import subprocess
import tempfile
import time
import tarfile
import zipfile
from pathlib import Path

from app.domain.paths import contained, safe_relative
from app.runtime.process_limits import limit_worker


def tool_path() -> str:
    configured = os.environ.get("WORK115_EXTRACTOR_PATH", "")
    if configured:
        return configured if Path(configured).is_file() else ""
    for candidate in (configured, shutil.which("7zz"), shutil.which("7z"),
                      r"C:\Program Files\7-Zip\7z.exe"):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return ""


def extractor_available(config=None):
    return component_info(config)["available"]


def component_info(config=None):
    """Read the selected executable's identity without running an extraction."""
    tool = config.extractor_path if config and config.extractor_path else tool_path()
    info = {"name": "7-Zip", "available": False, "version": None,
            "path": tool, "source": "https://www.7-zip.org/",
            "update_policy": "validated_release", "error": "EXTRACTOR_NOT_FOUND"}
    if not tool:
        return info
    try:
        result = subprocess.run([tool, "i"], capture_output=True, timeout=5,
                                encoding="utf-8", errors="replace")
        import re
        match = re.search(r"7-Zip\s+(?:\([^\r\n)]*\)\s+)?(\d+\.\d+)", result.stdout)
        if result.returncode == 0 and match:
            info.update(available=True, version=match[1], error=None)
        else:
            info.update(error="EXTRACTOR_EXECUTION_FAILED" if result.returncode else
                        "EXTRACTOR_VERSION_UNRECOGNIZED", exit_code=result.returncode)
    except subprocess.TimeoutExpired:
        info["error"] = "EXTRACTOR_PROBE_TIMEOUT"
    except OSError as exc:
        info.update(error="EXTRACTOR_EXECUTION_FAILED", os_error=exc.errno)
    return info


def seven_entries(source, password, max_bytes, max_files):
    tool = tool_path()
    if not tool:
        raise ValueError("EXTRACTOR_UNAVAILABLE")
    with tempfile.TemporaryFile() as metadata:
        listed = subprocess.Popen(
            [tool, "l", "-slt", "-ba", "-sccUTF-8", "-p" + (password or "__no_password__"),
             "--", str(source)], stdin=subprocess.DEVNULL, stdout=metadata, stderr=subprocess.DEVNULL,
        )
        started = time.monotonic()
        try:
            while listed.poll() is None:
                if os.fstat(metadata.fileno()).st_size > 32 * 1024**2 or time.monotonic() - started > 120:
                    raise ValueError("ARCHIVE_METADATA_LIMIT")
                time.sleep(0.05)
            if listed.returncode:
                raise ValueError("PASSWORD_OR_MISSING_PARTS_OR_CORRUPT_ARCHIVE")
            metadata.seek(0)
            raw = metadata.read(32 * 1024**2 + 1)
            if len(raw) > 32 * 1024**2:
                raise ValueError("ARCHIVE_METADATA_LIMIT")
        finally:
            if listed.poll() is None:
                listed.kill()
                listed.wait()
    entries = []
    total_size = 0
    seen = set()
    for block in raw.decode("utf-8", errors="strict").replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if "Path" not in fields:
            continue
        if any(fields.get(key) for key in ("Symbolic Link", "Hard Link", "Reparse Point")):
            raise ValueError("ARCHIVE_SPECIAL_FILE_REJECTED")
        if any(token.startswith(("l", "b", "c", "p", "s")) for token in
               fields.get("Attributes", "").split() if len(token) > 5) or (
            fields.get("Mode", "").startswith(("l", "b", "c", "p", "s"))
        ):
            raise ValueError("ARCHIVE_SPECIAL_FILE_REJECTED")
        key = safe_relative(fields["Path"]).as_posix().casefold()
        if key in seen:
            raise ValueError("DUPLICATE_ARCHIVE_ENTRY")
        seen.add(key)
        try:
            size = int(fields.get("Size") or 0)
        except ValueError:
            raise ValueError("ARCHIVE_METADATA_REJECTED") from None
        if size < 0:
            raise ValueError("ARCHIVE_METADATA_REJECTED")
        total_size += size
        entries.append((fields["Path"], size, fields.get("Folder") == "+" or
                        fields.get("Attributes", "").startswith("D")))
        if len(entries) > max_files:
            raise ValueError("ARCHIVE_FILE_LIMIT")
        if total_size > max_bytes:
            raise ValueError("ARCHIVE_SIZE_LIMIT")
    return tool, entries


def extract(source: Path, output: Path, max_bytes: int, max_files: int) -> dict:
    if max_bytes < 0 or max_files <= 0:
        raise ValueError("INVALID_EXTRACTION_LIMIT")
    max_bytes = max_bytes or 2**63 - 1
    output.mkdir(parents=True, exist_ok=False)
    reserve = int(os.environ.get("WORK115_LOCAL_RESERVE_BYTES", str(64 * 1024**2)))
    if shutil.disk_usage(output).free < reserve:
        raise ValueError("INSUFFICIENT_OUTPUT_SPACE")
    manifest = []
    name_mapping = {}
    total = 0
    seen = set()

    def publish_entry(name, size, directory, opener):
        nonlocal total
        relative = safe_relative(name)
        mapped = []
        for part in relative.parts:
            if len(part.encode("utf-8")) > 240:
                ext = Path(part).suffix
                ext = ext if len(ext.encode("utf-8")) <= 32 else ""
                suffix = "~" + hashlib.sha256((part + "\x00" + part).encode()).hexdigest()[:8] + ext
                prefix = part
                while len((prefix + suffix).encode("utf-8")) > 240:
                    prefix = prefix[:-1]
                part = prefix + suffix
            mapped.append(part)
        if tuple(mapped) != relative.parts:
            name_mapping[name] = "/".join(mapped)
            relative = safe_relative("/".join(mapped))
        if len(relative.parts) > 25:
            raise ValueError("ARCHIVE_DEPTH_LIMIT")
        target = contained(output, output.joinpath(*relative.parts))
        canonical = str(target).casefold()
        if canonical in seen:
            raise ValueError("DUPLICATE_ARCHIVE_ENTRY")
        seen.add(canonical)
        if directory:
            target.mkdir(parents=True, exist_ok=True)
            return
        total += size
        if total > max_bytes:
            raise ValueError("ARCHIVE_SIZE_LIMIT")
        if len(manifest) >= max_files:
            raise ValueError("ARCHIVE_FILE_LIMIT")
        target.parent.mkdir(parents=True, exist_ok=True)
        digest, copied = hashlib.sha256(), 0
        with contextlib.closing(opener()) as reader, target.open("xb") as writer:
            while chunk := reader.read(1024 * 1024):
                if shutil.disk_usage(output).free < reserve + len(chunk):
                    raise ValueError("INSUFFICIENT_OUTPUT_SPACE")
                copied += len(chunk)
                if copied > size or copied > max_bytes:
                    raise ValueError("ARCHIVE_ENTRY_SIZE_MISMATCH")
                writer.write(chunk)
                digest.update(chunk)
        if copied != size:
            raise ValueError("ARCHIVE_ENTRY_SIZE_MISMATCH")
        manifest.append({"path": relative.as_posix(), "size": copied, "sha256": digest.hexdigest()})

    password = os.environ.get("WORK115_ARCHIVE_PASSWORD", "").encode() or None
    native_zip = zipfile.is_zipfile(source)
    if native_zip:
        with zipfile.ZipFile(source) as probe:
            native_zip = all(entry.compress_type in {
                zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA,
            } for entry in probe.infolist())
    if native_zip:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > max_files:
                raise ValueError("ARCHIVE_FILE_LIMIT")
            if sum(x.file_size for x in entries) > max_bytes:
                raise ValueError("ARCHIVE_SIZE_LIMIT")
            for entry in entries:
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (
                    stat.S_ISREG(mode) or stat.S_ISDIR(mode)
                )):
                    raise ValueError("ARCHIVE_SPECIAL_FILE_REJECTED")
                publish_entry(entry.filename, entry.file_size, entry.is_dir(),
                              lambda e=entry: archive.open(e, pwd=password))
    elif tarfile.is_tarfile(source):
        with tarfile.open(source) as archive:
            for index, entry in enumerate(archive):
                if index >= max_files or (not entry.isfile() and not entry.isdir()):
                    raise ValueError("ARCHIVE_SPECIAL_FILE_OR_COUNT_REJECTED")
                publish_entry(entry.name, entry.size, entry.isdir(),
                              lambda e=entry: archive.extractfile(e))
    else:
        if os.environ.get("WORK115_EXTRACT_ENGINE", "7zip") != "7zip":
            raise ValueError("EXTRACTOR_UNAVAILABLE")
        tool, entries = seven_entries(source, password.decode() if password else "", max_bytes, max_files)
        for name, size, directory in entries:
            if directory:
                publish_entry(name, size, True, None)
                continue
            process = subprocess.Popen(
                [tool, "x", "-so", "-spd", "-y", "-p" + (
                    password.decode() if password else "__no_password__"
                ), "--", str(source), name],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            try:
                publish_entry(name, size, False, lambda: process.stdout)
                if process.wait(timeout=30):
                    raise ValueError("PASSWORD_OR_MISSING_PARTS_OR_CORRUPT_ARCHIVE")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    if not manifest:
        raise ValueError("ARCHIVE_HAS_NO_FILES")
    if os.environ.get("WORK115_DISABLE_RECURSION", "true") != "true":
        for item in list(manifest):
            nested = output / item["path"]
            if nested.suffix.lower() not in {".zip", ".7z", ".rar", ".tar", ".gz", ".iso"}:
                continue
            if nested.suffix.lower() == ".iso" and os.environ.get("WORK115_EXTRACT_ISOS", "true") != "true":
                continue
            if source.suffix.lower() == ".iso" and os.environ.get("WORK115_RECURSE_ISO", "false") != "true":
                continue
            depth = int(os.environ.get("WORK115_NESTED_DEPTH", "0"))
            if depth >= 25:
                raise ValueError("ARCHIVE_DEPTH_LIMIT")
            previous = os.environ.get("WORK115_NESTED_DEPTH")
            os.environ["WORK115_NESTED_DEPTH"] = str(depth + 1)
            try:
                with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
                    child = Path(temporary) / "content"
                    remaining = max_bytes - total
                    if remaining <= 0 or len(manifest) >= max_files:
                        raise ValueError("ARCHIVE_SIZE_LIMIT")
                    result = extract(nested, child, remaining, max_files - len(manifest))
                    for entry in result["files"]:
                        original = child / entry["path"]
                        publish_entry(entry["path"], entry["size"], False, lambda p=original: p.open("rb"))
                    name_mapping.update(result.get("name_mapping", {}))
            finally:
                if previous is None:
                    os.environ.pop("WORK115_NESTED_DEPTH", None)
                else:
                    os.environ["WORK115_NESTED_DEPTH"] = previous
    if os.environ.get("WORK115_SQUASH_ROOT", "false") == "true":
        while True:
            children = list(output.iterdir())
            if len(children) != 1 or not children[0].is_dir():
                break
            wrapper = children[0]
            # Use a sibling temporary directory to handle a child named like its parent.
            with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
                moved = Path(temporary) / "wrapper"
                wrapper.rename(moved)
                for child in moved.iterdir():
                    child.rename(output / child.name)
            for entry in manifest:
                entry["path"] = Path(entry["path"]).relative_to(wrapper.name).as_posix()
            name_mapping = {key: value.removeprefix(wrapper.name + "/")
                            for key, value in name_mapping.items()}
    result = {"engine": "7zip", "name_mapping": name_mapping, "files": manifest, "total_bytes": total, "source_size": source.stat().st_size,
              "source_mtime_ns": source.stat().st_mtime_ns}
    # Manifest outside extracted content cannot be overwritten by an archive entry.
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--max-files", type=int, required=True)
    args = parser.parse_args()
    try:
        limit_worker(1024**3, 1800)
        result = extract(Path(args.source), Path(args.output), args.max_bytes, args.max_files)
        print(json.dumps({"ok": True, "manifest": result}))
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) else (
            "PASSWORD_OR_CORRUPT_ARCHIVE" if isinstance(exc, (RuntimeError, zipfile.BadZipFile))
            else "EXTRACTION_FAILED"
        )
        print(json.dumps({"ok": False, "error": code[:100]}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
