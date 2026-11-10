#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# build.py - join src/NN_name.py into the single installable file `sbx`, and refuse if the layers are broken.
#
#     python3 build.py            write sbx
#     python3 build.py --check    don't write; exit 1 if sbx is not exactly what the fragments build (CI runs this)
#     python3 build.py -o FILE    write somewhere else
#
# Layout. Each fragment is one layer; the number in its name is the layer. Fragments are joined in number order,
# so the output is one flat module: names are shared, nothing is imported, tests can still exec() the whole file.
# Lines starting with "#|" are notes for readers of src/ and are dropped from the output; every other line is copied as is.
#
# Layer rule (checked here, the build fails otherwise):
#   1. A fragment may only use names defined in its own layer or a LOWER one. Never a higher one.
#   2. At import time (constants, default arguments, base classes) a name must already be defined above its use.
#   3. No top-level name is defined twice (a second definition would silently replace the first).
# "Use" means any reference that reaches a module-level name: inside function and class bodies too, but not a local
# variable that happens to share a name (the check uses Python's own symbol tables, not a text search).
import ast, os, re, symtable, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
OUT = os.path.join(ROOT, "sbx")
NAME = re.compile(r"\d\d_[a-z0-9_]+\.py")


def fragments():
    names = sorted(f for f in os.listdir(SRC) if f.endswith(".py"))
    bad = [f for f in names if not NAME.fullmatch(f)]
    if bad: sys.exit(f"build: src/ file names must look like 40_name.py: {', '.join(bad)}")
    if not names: sys.exit("build: no fragments in src/")
    out = []
    for f in names:
        with open(os.path.join(SRC, f)) as fh: lines = fh.read().split("\n")
        body = [l for l in lines if not l.startswith("#|")]
        while body and not body[0].strip(): body.pop(0)
        while body and not body[-1].strip(): body.pop()
        out.append((f, "\n".join(body)))
    return out


def bound(node):
    """The module-level names a top-level statement defines."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): return [node.name]
    if isinstance(node, (ast.Import, ast.ImportFrom)): return [(a.asname or a.name).split(".")[0] for a in node.names]
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]
    return []


def references(chunk):
    """(import_time, deferred): module-level names a statement reaches while it runs / only when its functions are called."""
    top, deferred = set(), set()

    def walk(table, is_top):
        for s in table.get_symbols():
            if is_top:
                if s.is_referenced() and not s.is_assigned() and not s.is_imported(): top.add(s.get_name())
            elif s.is_global() and s.is_referenced():
                deferred.add(s.get_name())
        for child in table.get_children(): walk(child, False)
    walk(symtable.symtable(chunk, "<fragment>", "exec"), True)
    return top, deferred


def check_layers(frags):
    """Returns a list of problems (empty when the layering holds)."""
    problems, owner, stmts = [], {}, []
    for layer, (fname, text) in enumerate(frags):
        tree = ast.parse(text, fname)
        lines = text.split("\n")
        for node in tree.body:
            first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
            idx = len(stmts)
            stmts.append((layer, fname, node, "\n".join(lines[first - 1:node.end_lineno])))
            for name in bound(node):
                if name in owner:
                    problems.append(f"{fname}:{node.lineno}: '{name}' is already defined in {frags[owner[name][0]][0]}")
                else:
                    owner[name] = (layer, idx)
    for idx, (layer, fname, node, chunk) in enumerate(stmts):
        now, later = references(chunk)
        for name in sorted(now | later):
            if name not in owner or owner[name][1] == idx: continue
            o_layer, o_idx = owner[name]
            if o_layer > layer:
                problems.append(f"{fname}:{node.lineno}: uses '{name}', which lives in the higher layer {frags[o_layer][0]}")
            elif name in now and o_idx > idx:
                problems.append(f"{fname}:{node.lineno}: uses '{name}' at import time, before it is defined")
    return problems


def build():
    frags = fragments()
    problems = check_layers(frags)
    if problems:
        print("build: layer rule broken, nothing written:", file=sys.stderr)
        for p in problems: print("  " + p, file=sys.stderr)
        sys.exit(1)
    text = "\n\n\n".join(body for _, body in frags) + "\n"
    ast.parse(text, "sbx")
    return text


def main(argv):
    check, out = False, OUT
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--check": check = True
        elif a == "-o" and args: out = args.pop(0)
        else: sys.exit("usage: build.py [--check] [-o FILE]")
    text = build()
    try:
        with open(out) as f: current = f.read()
    except OSError:
        current = None
    if check:
        if current != text:
            print(f"build: {os.path.relpath(out, ROOT)} is not what src/ builds. Run: python3 build.py", file=sys.stderr)
            sys.exit(1)
        print("build: up to date")
        return
    if current == text:
        print(f"build: {os.path.relpath(out, ROOT)} already up to date"); return
    with open(out, "w") as f: f.write(text)         # an existing file keeps its mode
    print(f"build: wrote {os.path.relpath(out, ROOT)} ({text.count(chr(10))} lines from {len(fragments())} fragments)")


if __name__ == "__main__":
    main(sys.argv[1:])
