"""Install pinned official Linux binaries; hashes observed by this project."""
import hashlib
import io
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path


RELEASES = {
    "amd64": ("x64", "8ea0fc8a135e7b848e80a4116fe22dff56c8c4518dde1f43cce67f4e340b437a"),
    "arm64": ("arm64", "39f8c9070c300a63c7484d9a983119ef3edf841e1ddf69f1affae29fdec5f612"),
}


def install(architecture):
    platform, digest = RELEASES[architecture]
    url = f"https://www.7-zip.org/a/7z2601-linux-{platform}.tar.xz"
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read(16 * 1024 * 1024)
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("7ZIP_CHECKSUM_MISMATCH")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:xz") as archive:
        for member, destination in (
            # Official static build avoids runtime libstdc++/loader dependencies.
            ("7zzs", Path("/usr/local/bin/7zz")),
            ("License.txt", Path("/usr/local/share/licenses/7zip/License.txt")),
        ):
            entry = archive.getmember(member)
            if not entry.isfile():
                raise ValueError("7ZIP_PACKAGE_REJECTED")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(entry) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
    Path("/usr/local/bin/7zz").chmod(0o755)


if __name__ == "__main__":
    install(sys.argv[1])
