#| 20_settings: Reading and writing the settings files (same format as sbx), the launch log tail, path rules, summaries.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---- settings files (same format as sbx) ----
def path_for(scope):
    return f"{CONF}/default.conf" if scope is None else f"{APPS}/{scope}.conf"


def strip(line):
    return re.sub(r"(^|\s)#.*$", "", line).strip()


def read_lines(scope):
    try:
        with open(path_for(scope), errors="replace") as f: return f.read().splitlines()
    except OSError: return []


def parse_bool(v):
    v = v.strip().lower()
    return True if v in ("1", "true", "yes", "on") else False if v in ("0", "false", "no", "off") else None


def parse(scope):
    sc, ls = {}, {k: [] for k in LISTS}
    for line in read_lines(scope):
        k, _, v = (x.strip() for x in strip(line).partition("="))
        if k in ls and v: ls[k].append(v)
        elif k in SWITCHES and parse_bool(v) is not None: sc[k] = parse_bool(v)
    return sc, ls


def save(scope, lines):
    p = path_for(scope)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w") as f: f.write("\n".join(lines) + ("\n" if lines else ""))
    os.replace(p + ".tmp", p)


def key_of(line):
    m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)       # digits too: x11
    return m.group(1) if m else None


def set_key(scope, key, val):
    lines = [l for l in read_lines(scope) if key_of(l) != key]
    if val is not None: lines.append(f"{key}={int(val)}")
    save(scope, lines)


def add_item(scope, key, v):
    lines = read_lines(scope)
    if re.search(r"(^|\s)#", v): return False        # sbx would read the rest as a comment
    if v not in parse(scope)[1][key]: save(scope, lines + [f"{key}={v}"])
    return True


def remove_item(scope, key, v):
    save(scope, [l for l in read_lines(scope) if not (key_of(l) == key and strip(l).partition("=")[2].strip() == v)])


def app_names():
    n = set()
    try: n |= {f[:-5] for f in os.listdir(APPS) if f.endswith(".conf")}
    except OSError: pass
    try: n |= {f for f in os.listdir(HOMES) if os.path.isdir(f"{HOMES}/{f}")}
    except OSError: pass
    return sorted(x for x in n if NAME_RE.match(x))


def portals_on(scope):
    """Is the portals switch on for this program (its own setting, else the default settings, else off)?"""
    sc = parse(scope)[0]
    if "portals" in sc or scope is None: return sc.get("portals", SWITCHES["portals"][2])
    return parse(None)[0].get("portals", SWITCHES["portals"][2])


def log_tail(scope=None, n=200):
    """The last n lines of sbx's launch log (only one program's lines when scope is given), or a short note."""
    try:
        with open(LOG, errors="replace") as f: lines = f.read().splitlines()
    except OSError:
        return "No launches logged yet."
    if scope: lines = [l for l in lines if f"[{scope}] " in l]
    return "\n".join(lines[-n:]) or "Nothing logged yet."


def log_lines(scope=None):
    """The lines of sbx's launch log (only one program's when scope is given); [] if there is no log."""
    try:
        with open(LOG, errors="replace") as f: lines = f.read().splitlines()
    except OSError:
        return []
    return [l for l in lines if f"[{scope}] " in l] if scope else lines


LOG_LINE_RE = re.compile(r"^(\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] )?(\[[^\]]+\] )?(.*)$", re.S)


def log_line_parts(line):
    """One log line as (timestamp, "[name] ", text, kind). Timestamp and name may be empty (a program's own output has no
    timestamp); kind is a key of LOG_COLORS or "" for plain text."""
    ts, name, text = LOG_LINE_RE.match(line).groups()
    kind = next((k for pat, k in LOG_KINDS if re.search(pat, text, re.I)), "")
    return ts or "", name or "", text, kind


def effective_switches(scope):
    """The switch values a program runs with: built-in defaults, then default.conf, then the program's own file."""
    v = {k: d for k, (_, _, d) in SWITCHES.items()}
    v.update(parse(None)[0])
    if scope is not None: v.update(parse(scope)[0])
    return v


def last_run(lines):
    """(lines of the most recent run, its exit code or None). A run starts at a START line and ends at an EXIT line."""
    start = 0
    for i in range(len(lines) - 1, -1, -1):
        if "] START allow=[" in lines[i]:
            start = i + 1
            break
    run_lines = lines[start:]
    code = None
    for ln in reversed(run_lines):
        m = re.search(r"\] EXIT code=(-?\d+)", ln)
        if m:
            code = int(m.group(1))
            break
    return run_lines, code


def log_hints(lines, current):
    """Suggestions for a program, as [(setting, wanted value, title, why)]. Only for a last run that ended with an error;
    LINES are that program's log lines, CURRENT its effective switches (see effective_switches)."""
    run_lines, code = last_run(lines)
    if code in (None, 0): return []
    text = "\n".join(run_lines[-300:])[-200_000:]
    out, seen = [], set()
    for pat, key, want, title, why, needs in LOG_HINTS:
        if key in seen or current.get(key) == want: continue
        if any(current.get(k) != v for k, v in needs.items()): continue
        if re.search(pat, text, re.I):
            out.append((key, want, title, why))
            seen.add(key)
    return out


def summary(scope):
    sc, ls = parse(scope)
    parts = [f"{SHORT[k]} {'on' if sc[k] else 'off'}" for k in SWITCHES if k in sc]
    n = len(ls["whitelist"]) + len(ls["read_only"])
    if n: parts.append(f"{n} shared folder{'s' if n != 1 else ''}")
    return " · ".join(parts) or "Uses the default settings"


def path_problem(p):
    """Why sbx would refuse this path, or None."""
    if not p: return "That location is not a local path."
    p = os.path.realpath(p)
    for g in (CONF, HOMES):
        if p == g or p.startswith(g + "/") or g.startswith(p + "/"):
            return "That would expose sbx's own settings or private homes."
    if p == HOME: return "That is your whole home folder. Pick a folder inside it."
    if not parse(CURRENT[0] if CURRENT else None)[0].get("allow_sensitive", False):
        for rel in SENSITIVE:
            g = f"{HOME}/{rel}"
            if p == g or p.startswith(g + "/") or g.startswith(p + "/"):
                return f"That is or contains ~/{rel}, which holds secrets. Turn on \"Allow sharing secret folders\" first."
    if p.startswith(HOME + "/") or p.startswith(OUTSIDE): return None
    return "sbx only shares paths inside your home, on /mnt or /media, or in /tmp."


def tilde(p):
    return "~/" + p[len(HOME) + 1:] if p.startswith(HOME + "/") else p
