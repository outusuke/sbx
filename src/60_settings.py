#| 60_settings: Reading settings files and merging them with command-line flags.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- settings
def parse_bool(v):
    v = v.strip().lower()
    return True if v in ("1", "true", "yes", "on") else False if v in ("0", "false", "no", "off") else None


def env_name_ok(k):
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k))


def syscall_ok(n):
    return n == "io_uring" or n in EXTRA_SYSCALLS


def parse_conf(path):
    """Plain key=value file. Returns (switches, lists, limits). '#' starts a comment. A line that makes no sense is
    reported and skipped, so a typo like net=maybe doesn't silently mean something else."""
    sc, ls, lim = {}, {k: [] for k in LISTS}, {}
    try:
        with open(path, errors="replace") as f: lines = f.read().splitlines()      # a stray byte must not crash sbx
    except OSError:
        return sc, ls, lim
    for n, line in enumerate(lines, 1):
        line = re.sub(r"(^|\s)#.*$", "", line).strip()
        if "=" not in line: continue
        k, _, v = (x.strip() for x in line.partition("="))
        at = f"{path}:{n}"
        if k in ls:
            if k == "env" and not env_name_ok(v): warn(f"{at}: '{v}' is not a variable name, ignored")
            elif k == "setenv" and ("=" not in v or not env_name_ok(v.partition("=")[0])): warn(f"{at}: setenv needs NAME=value, ignored")
            elif k == "allow_syscall" and not syscall_ok(v): warn(f"{at}: can't allow '{v}': not one of {', '.join(sorted(EXTRA_SYSCALLS))}, io_uring")
            elif v: ls[k].append(v)
        elif k in SCALARS:
            b = parse_bool(v)
            if b is None: warn(f"{at}: {k}={v} is not 0 or 1, ignored")
            else: sc[k] = b
        elif k in LIMITS:
            if v == "" or re.fullmatch(LIMITS[k], v): lim[k] = v
            else: warn(f"{at}: {k}={v} is not a valid limit, ignored")
        else:
            warn(f"{at}: ignoring unknown setting '{k}'")
    return sc, ls, lim


def load_conf(name, ignore=False):
    """Built-in defaults < default.conf < apps/NAME.conf. Lists add up. Returns (settings, where each value came from)."""
    cfg = {**SCALARS, **{k: "" for k in LIMITS}, **{k: [] for k in LISTS}}
    src = {k: "default" for k in cfg}
    if ignore: return cfg, src
    for label, path in (("default.conf", f"{CONF}/default.conf"), (f"apps/{name}.conf", f"{CONF}/apps/{name}.conf")):
        sc, ls, lim = parse_conf(path)
        for k, v in {**sc, **lim}.items(): cfg[k], src[k] = v, label
        for k in LISTS:
            for v in ls[k]:
                if v not in cfg[k]: cfg[k].append(v); src[(k, v)] = label
    return cfg, src


def effective(name, o):
    """The settings for one program: files, then command-line flags on top."""
    cfg, src = load_conf(name, o.get("ignore_config"))
    for k in list(SCALARS) + list(LIMITS):
        if k in o: cfg[k], src[k] = o[k], "flag"
    for k in ("env", "setenv", "allow_syscall"):
        for v in o.get(k, []):
            if v not in cfg[k]: cfg[k].append(v); src[(k, v)] = "flag"
    return cfg, src
