#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Jacek Mariański
"""The installer site (GitHub Pages): web/ with the firmwares and the module beside it.

  python3 web/make_site.py --felucca DIR --sloop DIR [--out build/site] [--dirty]

DIR: a Felucca / SLOOP checkout on its fext branch, built with the socket (Felucca: FELUCCA_FEXT=1 tools/build.py,
build/felucca-fext.fwsc; SLOOP: tools/build.py --fext, build/fext/felucca-fext.fwsc). The module: build/fble.img
(sh build.sh). Each checkout (and the module's) must be clean and its commit on a public remote (the fork's): the
page links every file to the commit it was built from, and to its licences.

  out/index.html            web/index.html, the metadata in it
  out/*.js                  fext.js (MPL-2.0), fm1ota.js and fm1pkg.js (SLOOP's, GPL-3.0)
  out/fw/                   felucca-fext.fwsc, sloop-fext.fwsc, fble.img
  out/licenses/<name>/      each one's LICENSE, LICENSES/, LICENSING.md or NOTICE, the build's ATTRIBUTION.txt
"""
import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BLOCKS, BLK, KEEP = 20, 0x30, 0x2F                  # fm1pkg.js productOf


def git(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True, text=True).stdout.strip()


def https(url):
    """git@github.com:a/b.git -> https://github.com/a/b"""
    m = re.match(r"(?:git@|ssh://git@|https://)([^:/]+)[:/](.+?)(?:\.git)?$", url)
    if not m:
        sys.exit(f"make_site: {url}: not a remote I can link to")
    return f"https://{m.group(1)}/{m.group(2)}"


DIRTY = False                                       # --dirty: a preview, the module's checkout may have changes


def source(d, name, ignore=()):
    """{name, url, commit} of checkout d: clean (but for ignore), its commit on a remote"""
    dirty = [l for l in git(d, "status", "--porcelain").splitlines() if l[3:] not in ignore]
    if dirty and DIRTY and d == ROOT:
        print(f"make_site: --dirty: {name} has changes not committed (a preview, not to publish)")
    elif dirty:
        sys.exit(f"make_site: {d} has changes not committed:\n  " + "\n  ".join(dirty))
    commit = git(d, "rev-parse", "HEAD")
    remotes = git(d, "branch", "-r", "--contains", commit).split()
    if not remotes:
        sys.exit(f"make_site: {d}: {commit[:7]} is on no remote (push it first)")
    remote = next((r.split("/")[0] for r in remotes if r.startswith("fork/")), remotes[0].split("/")[0])
    url = https(git(d, "remote", "get-url", remote))
    return {"name": name, "url": f"{url}/tree/{commit}", "commit": commit}


def product_of(raw):
    s = ""
    for i in range(BLOCKS):
        m = raw[i * BLK + KEEP]
        if m != 0x7D:
            s += chr((m - i - 1) & 0xFF)
    return s


def sha256(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def version_of(path, name):
    m = re.search(rf'#define\s+{name}\s+"([^"]+)"', path.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f"make_site: no {name} in {path}")
    return m.group(1)


def licenses(out, name, title, files):
    """out/licenses/name/: the files, and an index.html listing them"""
    d = out / "licenses" / name
    d.mkdir(parents=True)
    links = []
    for src in files:
        if src.is_dir():
            (d / src.name).mkdir()
            for f in sorted(src.iterdir()):
                shutil.copy2(f, d / src.name / f.name)
                links.append(f"{src.name}/{f.name}")
        elif src.exists():
            shutil.copy2(src, d / src.name)
            links.append(src.name)
    items = "".join(f'<li><a href="{html.escape(l)}">{html.escape(l)}</a></li>' for l in links)
    (d / "index.html").write_text(
        f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)} licences</title><style>body{{font:16px/1.5 system-ui,sans-serif;margin:24px 16px;"
        f"background:#fff;color:#1d1d1b}}@media (prefers-color-scheme:dark){{body{{background:#161614;color:#ecebe6}}"
        f"a{{color:#7aa2ff}}}}</style><h1>{html.escape(title)}</h1><ul>{items}</ul>\n", encoding="utf-8")
    return f"licenses/{name}/"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--felucca", type=Path, required=True)
    ap.add_argument("--sloop", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "site")
    ap.add_argument("--dirty", action="store_true", help="a preview: the module's checkout may have changes")
    a = ap.parse_args()
    global DIRTY
    DIRTY = a.dirty
    fel, slp, out = a.felucca.resolve(), a.sloop.resolve(), a.out.resolve()

    builds = [
        ("felucca", "Felucca + fext", fel, fel / "build" / "felucca-fext.fwsc", fel / "build" / "ATTRIBUTION.txt",
         version_of(fel / "firmware" / "src" / "felucca.c", "FELUCCA_VERSION"), https(git(fel, "remote", "get-url", "origin"))),
        ("sloop", "SLOOP + fext", slp, slp / "build" / "fext" / "felucca-fext.fwsc", slp / "build" / "fext" / "ATTRIBUTION.txt",
         version_of(slp / "firmware" / "src" / "ui.c", "FELUCCA_VERSION").replace("SLOOP ", ""),
         https(git(slp, "remote", "get-url", "origin"))),
    ]
    img = ROOT / "build" / "fble.img"
    for p in [b[3] for b in builds] + [img]:
        if not p.exists():
            sys.exit(f"make_site: {p} is not built")

    if out.exists():
        shutil.rmtree(out)
    (out / "fw").mkdir(parents=True)
    meta = {"firmware": [], "module": None}
    for key, name, d, pkg, attribution, version, base in builds:
        srcs = [source(d, Path(https(git(d, "remote", "get-url", "fork"))).name)]
        if (d / "firmware" / "bsp" / ".git").exists():
            srcs.append(source(d / "firmware" / "bsp", "felucca-bsp"))
        dst = out / "fw" / f"{key}-fext.fwsc"
        shutil.copy2(pkg, dst)
        meta["firmware"].append({
            "name": name, "version": version, "product": product_of(dst.read_bytes()), "base": base.split("/", 3)[3],
            "file": f"fw/{dst.name}", "sha256": sha256(dst), "source": srcs,
            "licenses": licenses(out, key, name, [d / "LICENSE", d / "LICENSING.md", d / "LICENSES", attribution]),
        })
    shutil.copy2(img, out / "fw" / "fble.img")
    meta["module"] = {
        "name": "fble", "file": "fw/fble.img", "sha256": sha256(out / "fw" / "fble.img"),
        "source": [source(ROOT, "fble", ignore=("fm1-base",)),
                   {"name": "fm1-base", "url": "https://github.com/charlesvestal/fm1-base/tree/" + git(ROOT / "fm1-base", "rev-parse", "HEAD"),
                    "commit": git(ROOT / "fm1-base", "rev-parse", "HEAD")}],
        "licenses": licenses(out, "fble", "fble", [ROOT / "NOTICE", ROOT / "LICENSES"]),
    }

    page = (HERE / "index.html").read_text(encoding="utf-8")
    if "/*META*/{}" not in page:
        sys.exit("make_site: web/index.html has no /*META*/{}")
    (out / "index.html").write_text(page.replace("/*META*/{}", json.dumps(meta, ensure_ascii=False)), encoding="utf-8")
    for js in ("fext.js", "fm1ota.js", "fm1pkg.js"):
        shutil.copy2(HERE / js, out / js)
    (out / ".nojekyll").write_text("")
    for f in meta["firmware"] + [meta["module"]]:
        print(f"  {f['file']:24} {f['sha256'][:12]}  " + ", ".join(f"{s['name']} {s['commit'][:7]}" for s in f["source"]))
    print(f"site     {out}")


if __name__ == "__main__":
    main()
