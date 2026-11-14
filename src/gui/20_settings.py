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


def settings_files():
    """Every settings file that exists: default.conf and apps/*.conf."""
    out = [p for p in (path_for(None),) if os.path.lexists(p)]
    try: out += [f"{APPS}/{f}" for f in sorted(os.listdir(APPS)) if f.endswith(".conf") and not f.startswith(".")]
    except OSError: pass
    return out


def reset_settings(scope):
    """Delete a settings file. Writing an empty one is not the same: a program that has a file loses the defaults `sbx open` gives one without."""
    try: os.unlink(path_for(scope)); return True
    except OSError: return False


def reset_all_settings():
    n = 0
    for p in settings_files():
        try: os.unlink(p); n += 1
        except OSError: pass
    return n


def forget_program(name):
    """Take a program off the list: its private home goes to the Trash first (restorable; if that fails it raises GLib.Error and nothing
    else is touched), then its settings file, machine-id, origin note and any decision waiting on it."""
    home = f"{HOMES}/{name}"
    if os.path.isdir(home) and not os.path.islink(home): Gio.File.new_for_path(home).trash(None)
    reset_settings(name)
    for p in (f"{CONF}/ids/{name}", f"{CONF}/origins/{name}", f"{CONF}/pending/{name}"):
        try: os.unlink(p)
        except OSError: pass


def key_of(line):
    m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)       # digits too: x11
    return m.group(1) if m else None


def set_key(scope, key, val):
    lines = [l for l in read_lines(scope) if key_of(l) != key]
    if val is not None: lines.append(f"{key}={int(val)}")
    save(scope, lines)


def raw_list(scope, key):
    """The values of the KEY= lines of a settings file, for lists that parse() does not keep (setenv)."""
    return [strip(l).partition("=")[2].strip() for l in read_lines(scope) if key_of(l) == key]


def add_item(scope, key, v):
    lines = read_lines(scope)
    if re.search(r"(^|\s)#", v): return False        # sbx would read the rest as a comment
    if v not in raw_list(scope, key): save(scope, lines + [f"{key}={v}"])
    return True


def remove_item(scope, key, v):
    save(scope, [l for l in read_lines(scope) if not (key_of(l) == key and strip(l).partition("=")[2].strip() == v)])


def env_problem(name, value=None):
    """Why this variable can't go into the settings file, or None. VALUE is None for a variable that is passed through (env=NAME)."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name): return "A variable name has letters, digits and _, and doesn't start with a digit."
    if name in ENV_PROTECTED: return f"{name} can't be changed: sbx sets it itself."
    if value is not None and re.search(r"(^|\s)#", value): return "A value can't contain ' #': sbx would read the rest as a comment."
    return None


def parse_env_entry(text):
    """(list key, value, problem) for what is typed in a variable row: NAME=value sets a variable (setenv=), a bare NAME passes
    your own value through (env=). The problem is None when it can be stored."""
    name, eq, val = text.strip().partition("=")
    name, val = name.strip(), val.strip()
    if not name and not eq: return None, None, "Type NAME=value to set a variable, or just NAME to pass your own value through."
    bad = env_problem(name, val if eq else None)
    if bad: return None, None, bad
    return ("setenv", f"{name}={val}", None) if eq else ("env", name, None)


def set_env_entry(scope, text, old=None):
    """Add the variable typed in a row, or, with old=(list key, value), change that entry where it stands in the file. One value
    per variable: another entry of the same list with the same name is replaced too. Other lines stay as they are.
    Returns why it can't be stored, or None."""
    key, v, bad = parse_env_entry(text)
    if bad: return bad
    name = v.partition("=")[0]
    out, placed = [], False
    for l in read_lines(scope):
        k, val = key_of(l), strip(l).partition("=")[2].strip()
        if k in ("setenv", "env") and ((k, val) == old or (k == key and val.partition("=")[0] == name)):
            if not placed: out.append(f"{key}={v}"); placed = True
            continue
        out.append(l)
    if not placed: out.append(f"{key}={v}")
    save(scope, out)
    return None


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


def needs(key):
    """The switches of which at least one must be on before KEY does anything, or ()."""
    return ("portals",) if key.startswith("portal_") else NEEDS.get(key, ())


def is_on(scope, key):
    """The value a switch has for this program: its own setting, else the default settings, else the built-in default."""
    sc = parse(scope)[0]
    if key in sc: return sc[key]
    if scope is not None and key in (dsc := parse(None)[0]): return dsc[key]
    return SWITCHES[key][2] if key in SWITCHES else False


NET_CHOICES = (("Full access", "The internet, this computer's own services and your local network"),
               ("Internet only", "The internet, but not services running on this computer (databases, dev servers). Other devices on your local network are still reachable. Needs pasta (passt)"),
               ("No network", "The program can't reach any network"))


def net_choice(scope):
    """Index into NET_CHOICES for the two switches net and net_private, as this program runs."""
    if "net_private" not in SWITCHES: return 0 if is_on(scope, "net") else 2
    return 2 if not is_on(scope, "net") else 1 if is_on(scope, "net_private") else 0


def capabilities(scope):
    """One sentence on what a program can do, from its effective settings (own file, else default.conf, else built-in)."""
    net = NET_CHOICES[net_choice(scope)][0].lower()
    on = [words for key, words in CAN if key in SWITCHES and is_on(scope, key)]
    lists = [parse(None)[1], parse(scope)[1]] if scope is not None else [parse(None)[1]]
    shared = {v: k for ls in lists for k in LISTS for v in ls[k]}
    text = f"Network: {net}. " + ("Can use: " + ", ".join(on) + ". " if on else "Can use no devices or desktop services. ")
    if shared: text += f"Shares {len(shared)} folder{'s' if len(shared) != 1 else ''} or file{'s' if len(shared) != 1 else ''}. "
    return text.strip() + (" Its home is erased on exit." if is_on(scope, "private") else "")


def program_name(text):
    """What a typed program name becomes: the same cleaning sbx applies."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(text.strip())).lstrip("._-")[:64]


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


def effective_lists(scope):
    """The list values a program runs with: default.conf and the program's own file add up."""
    return {k: raw_list(None, k) + (raw_list(scope, k) if scope is not None else []) for k in HINT_LISTS}


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


def log_hints(lines, current, lists=None):
    """Suggestions for a program, as [(setting, wanted value, title, why)]. Only for a last run that ended with an error;
    LINES are that program's log lines, CURRENT its effective switches (see effective_switches), LISTS its effective lists
    (see effective_lists). Setting None is advice with no button."""
    run_lines, code = last_run(lines)
    if code in (None, 0): return []
    text = "\n".join(run_lines[-300:])[-200_000:]
    out, seen = [], set()
    for pat, key, want, title, why, needs in LOG_HINTS:
        if ((key, want) if key else title) in seen: continue
        if key in HINT_LISTS:                                     # already there (a setenv counts when the variable is set at all)
            have = (lists or {}).get(key, [])
            if want in have or (key == "setenv" and any(h.partition("=")[0] == want.partition("=")[0] for h in have)): continue
        elif key is not None and current.get(key) == want: continue
        if any(current.get(k) != v for k, v in needs.items()): continue
        if re.search(pat, text, re.I):
            out.append((key, want, title, why))
            seen.add((key, want) if key else title)
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
