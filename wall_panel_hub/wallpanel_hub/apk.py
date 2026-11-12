"""What an uploaded Android app is, read from its files (docs/kiosk-plan.md, K6).

An app comes as one .apk, as several split .apk files, or as a bundle (.apks from bundletool, .xapk)
that holds them. Each .apk is a zip whose AndroidManifest.xml is in Android's binary XML; this reads
the few attributes the hub needs from it (package, version, lowest Android, split name) with no new
library. The app's name is in its resources, not read here: the panel says it once installed.
"""

import struct
import zipfile
from pathlib import Path

MANIFEST = "AndroidManifest.xml"
# Android's ids for the attributes an obfuscated manifest leaves unnamed.
ATTR_IDS = {0x0101021B: "versionCode", 0x0101021C: "versionName", 0x0101020C: "minSdkVersion"}
TYPE_STRING, TYPE_INT_DEC, TYPE_INT_HEX = 0x03, 0x10, 0x11


class NotAnApp(ValueError):
    """Not an Android app the hub can hand out; said to the installer as it is."""


def _strings(data: bytes, start: int) -> list[str]:
    count, _styles, flags, strings_start, _ = struct.unpack_from("<5I", data, start + 8)
    header = struct.unpack_from("<H", data, start + 2)[0]
    offsets = struct.unpack_from(f"<{count}I", data, start + header)
    utf8 = bool(flags & 0x100)
    out = []
    for off in offsets:
        p = start + strings_start + off
        if utf8:
            # Its length in UTF-16 units, then in bytes: each one or two bytes long.
            p += 2 if data[p] & 0x80 else 1
            n = data[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | data[p + 1]
                p += 1
            p += 1
            out.append(data[p:p + n].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", data, p)[0]
            p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, p)[0]
                p += 2
            out.append(data[p:p + 2 * n].decode("utf-16-le", "replace"))
    return out


def manifest(data: bytes) -> dict:
    """The attributes of <manifest> and <uses-sdk> in a binary AndroidManifest.xml."""
    try:
        if struct.unpack_from("<H", data, 0)[0] != 0x0003:
            raise NotAnApp("its manifest is not Android's binary XML")
        strings: list[str] = []
        ids: list[int] = []
        found: dict[str, dict] = {}
        p = struct.unpack_from("<H", data, 2)[0]
        while p + 8 <= len(data):
            kind, header, size = struct.unpack_from("<HHI", data, p)
            if size < 8:
                break
            if kind == 0x0001:
                strings = _strings(data, p)
            elif kind == 0x0180:
                ids = list(struct.unpack_from(f"<{(size - header) // 4}I", data, p + header))
            elif kind == 0x0102:
                name_i, attr_start, attr_size, count = struct.unpack_from("<IHHH", data, p + header + 4)
                tag = strings[name_i]
                if tag in ("manifest", "uses-sdk") and tag not in found:
                    attrs = {}
                    for i in range(count):
                        a = p + header + attr_start + i * attr_size
                        _ns, an, raw, _sz, _r, dtype, value = struct.unpack_from("<IIIHBBI", data, a)
                        key = ATTR_IDS.get(ids[an]) if an < len(ids) and ids[an] in ATTR_IDS else strings[an]
                        if dtype == TYPE_STRING:
                            attrs[key] = strings[value]
                        elif dtype in (TYPE_INT_DEC, TYPE_INT_HEX):
                            attrs[key] = value
                        elif raw != 0xFFFFFFFF:
                            attrs[key] = strings[raw]
                    found[tag] = attrs
            p += size
    except (struct.error, IndexError) as e:
        raise NotAnApp("its manifest cannot be read") from e
    if "manifest" not in found:
        raise NotAnApp("its manifest has no <manifest>")
    return {**found.get("uses-sdk", {}), **found["manifest"]}


def read(path: Path) -> dict:
    """One .apk: {package, version_code, version_name, min_sdk, split}."""
    try:
        with zipfile.ZipFile(path) as z:
            data = z.read(MANIFEST)
    except (zipfile.BadZipFile, KeyError, OSError) as e:
        raise NotAnApp(f"{path.name} is not an Android app") from e
    m = manifest(data)
    package, code = m.get("package"), m.get("versionCode")
    if not isinstance(package, str) or not package or not isinstance(code, int):
        raise NotAnApp(f"{path.name} has no package or version")
    split = m.get("split")
    min_sdk = m.get("minSdkVersion")
    return {
        "package": package,
        "version_code": code,
        "version_name": str(m.get("versionName") or code),
        "min_sdk": min_sdk if isinstance(min_sdk, int) else None,
        "split": split if isinstance(split, str) and split else None,
    }


def unpack(bundle: Path, into: Path, limit: int) -> list[Path]:
    """The .apk files in an .apks or .xapk bundle, written to [into]. Standalone APKs (for Android
    before 5) are left out; an app with its OBB data is refused, as the hub does not place it."""
    try:
        with zipfile.ZipFile(bundle) as z:
            infos = z.infolist()
            if any(i.filename.lower().endswith(".obb") for i in infos):
                raise NotAnApp(f"{bundle.name} carries OBB data, which panels do not get")
            apks = [i for i in infos if i.filename.lower().endswith(".apk") and not i.filename.startswith("standalones/")]
            if not apks:
                raise NotAnApp(f"{bundle.name} holds no .apk")
            if sum(i.file_size for i in apks) > limit:
                raise NotAnApp("the app is larger than allowed")
            out = []
            for n, info in enumerate(apks):
                dest = into / f"bundle{n}.apk"
                with z.open(info) as src, open(dest, "wb") as f:
                    while chunk := src.read(1 << 20):
                        f.write(chunk)
                out.append(dest)
            return out
    except zipfile.BadZipFile as e:
        raise NotAnApp(f"{bundle.name} is not a bundle") from e
