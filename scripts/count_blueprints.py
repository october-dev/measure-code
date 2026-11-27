#!/usr/bin/env python3
# From the studio data assessment skill (Appendix: Blueprint counter), used with the author's permission.
# Vendored verbatim below this comment; measure-code runs it read-only and parses its TOTAL line.
"""Count Unreal Blueprints and Blueprint nodes in one project folder, read-only.
Python 3.8+, no packages, no Unreal install. Usage: python count_blueprints.py <project folder>
Prints one line per top-level Content folder and per plugin, then totals."""
import collections, hashlib, os, struct, sys

TAG = 0x9E2A83C1
COOKED = 0x80000000  # PKG_FilterEditorOnly: set on packaged-build assets
BP_CLASSES = {"Blueprint", "WidgetBlueprint", "AnimBlueprint", "EditorUtilityBlueprint",
              "EditorUtilityWidgetBlueprint", "ControlRigBlueprint", "GameplayAbilityBlueprint",
              "LevelScriptBlueprint"}
OTHER = (("BehaviorTreeGraphNode", "Behavior Tree"), ("BehaviorTreeDecoratorGraphNode", "Behavior Tree"),
         ("SoundCueGraphNode", "Sound Cue"), ("Niagara", "Niagara"),
         ("EnvironmentQueryGraphNode", "EQS"), ("MaterialGraphNode", "Material"))
SKIP = {".git", ".svn", ".p4", "Intermediate", "Binaries", "Saved", "DerivedDataCache", "node_modules"}
MAX = 256 * 1024 * 1024


class R:
    def __init__(self, d): self.d, self.o = d, 0
    def i32(self):
        v = struct.unpack_from("<i", self.d, self.o)[0]; self.o += 4; return v
    def fstr(self):
        n = self.i32()
        if n < 0:
            s = self.d[self.o:self.o - 2 * n].decode("utf-16-le", "replace"); self.o += -2 * n
        else:
            s = self.d[self.o:self.o + n].decode("latin-1"); self.o += n
        return s.rstrip("\x00")


def names(data, size, hash_first):
    r = R(data)
    if r.i32() & 0xFFFFFFFF != TAG: return None
    legacy = r.i32()
    if legacy != -4: r.i32()
    ue4 = r.i32()
    if legacy <= -8: r.i32()
    r.i32()
    if hash_first: r.o += 24  # UE 5.6+
    if legacy <= -2:
        n = r.i32()
        if not 0 <= n < 10000: raise ValueError
        if legacy < -5: r.o += n * 20
        elif legacy < -2:
            for _ in range(n): r.o += 20; r.fstr()
        else: r.o += n * 8
    if not hash_first: r.i32()
    r.fstr()
    flags = struct.unpack_from("<I", data, r.o)[0]; r.o += 4
    count, offset = r.i32(), r.i32()
    if not (0 < count < 2_000_000 and 0 < offset < size): raise ValueError
    r.o, out = offset, []
    for _ in range(count):
        out.append(r.fstr())
        if ue4 >= 504: r.o += 4
    return flags, out


def read(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            data = fh.read(64 * 1024)
            if data.startswith(b"version https://git-lfs"): return ("lfs pointer", 0, None)
            info = None
            for step in (1024 * 1024, None, "stop"):
                for hf in (False, True):
                    try:
                        info = names(data, size, hf)
                        if info and all(n and n.isprintable() for n in info[1][:20]): break
                        info = None
                    except (struct.error, ValueError, UnicodeDecodeError):
                        info = None
                if info or len(data) >= size or step == "stop" or (step is None and size > MAX): break
                fh.seek(0); data = fh.read(step) if step else fh.read()
            if not info: return ("unreadable", 0, None)
            flags, nm = info
            if flags & COOKED: return ("cooked", 0, None)
            ns = set(nm)
            is_bp = bool(ns & BP_CLASSES) or any(n.startswith(("K2Node_", "AnimGraphNode_")) for n in nm)
            other = next((lab for p, lab in OTHER if any(n.startswith(p) for n in nm)), None)
            if "NodeGuid" not in ns or "StructProperty" not in ns or not (is_bp or other):
                return ("asset", 0, None)
            if len(data) < size:
                if size > MAX: return ("unreadable", 0, None)
                fh.seek(0); data = fh.read()
            idx = {n: i for i, n in enumerate(nm)}
            nodes = data.count(struct.pack("<iiii", idx["NodeGuid"], 0, idx["StructProperty"], 0))
            if is_bp: return ("blueprint", nodes, hashlib.sha1(data).hexdigest())
            return ("graph:" + other, nodes, None)
    except OSError:
        return ("unreadable", 0, None)


def group(rel):
    p = rel.replace("\\", "/").split("/")
    if "Plugins" in p and p.index("Plugins") + 1 < len(p): return "Plugins/" + p[p.index("Plugins") + 1]
    if "Content" in p and p.index("Content") + 2 < len(p): return "Content/" + p[p.index("Content") + 1]
    return "Content (top level)"


def main(root):
    rows, seen = collections.defaultdict(collections.Counter), set()
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in (".uasset", ".umap"): continue
            path = os.path.join(d, f)
            kind, nodes, sha = read(path)
            c = rows[group(os.path.relpath(path, root))]
            c["files"] += 1
            if kind == "blueprint":
                if sha in seen: c["duplicate blueprints"] += 1; continue
                seen.add(sha)
                key = "level blueprint" if ext == ".umap" else "blueprint"
                c[key + "s"] += 1; c[key + " nodes"] += nodes
            elif kind.startswith("graph:"):
                c[kind[6:] + " graphs"] += 1; c[kind[6:] + " nodes"] += nodes
            elif kind != "asset":
                c[kind] += 1
    total = collections.Counter()
    for g in sorted(rows):
        total.update(rows[g])
        print(g + ": " + ", ".join(f"{k} {v:,}" for k, v in sorted(rows[g].items())))
    print("TOTAL: " + ", ".join(f"{k} {v:,}" for k, v in sorted(total.items())))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
