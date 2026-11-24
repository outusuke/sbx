#| 40_seccomp: SECURITY-CRITICAL: the system-call blocklist and the raw BPF program built from it.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- system-call filter
def blocked_numbers(machine, allow=()):
    """The numbers of the blocked system calls on this architecture."""
    col = ARCHES[machine][1]
    allowed = set()
    for n in allow: allowed.update(IO_URING if n == "io_uring" else [n])
    nrs = [t[col] for t in BASE_SYSCALLS.values() if t[col] is not None]
    nrs += [t[col] for n, t in EXTRA_SYSCALLS.items() if n not in allowed and t[col] is not None]
    return nrs


def blocked_numbers_i386(allow=()):
    allowed = set()
    for n in allow: allowed.update(IO_URING if n == "io_uring" else [n])
    return [nr for name, nr in I386_SYSCALLS.items() if name not in allowed] + [I386_SOCKETCALL_NR]


def arch_block(nrs, sock, x32):
    """The checks for one ABI. Jumps stay inside the block; the last two instructions are allow and deny."""
    fam, n = SOCKET_FAMILIES, len(nrs)
    if n + len(fam) > 240: raise ValueError("too many blocked system calls: a BPF jump offset is 8 bits")
    b = len(fam) + 3                                            # length of the socket part below
    ins = [(0x20, 0, 0, 0)]
    if x32: ins += [(0x35, n + b + 1, 0, 0x40000000)]           # x32 calls: deny
    ins += [(0x15, 0, b - 1, sock), (0x20, 0, 0, 16)]           # socket(): load the family (low half of arg 0)
    ins += [(0x15, n + b - 3 - i, 0, f) for i, f in enumerate(fam)]    # listed family: jump to allow
    ins += [(0x06, 0, 0, 0x00050000 | errno.EPERM)]             # any other family: deny
    ins += [(0x15, n - i, 0, nr) for i, nr in enumerate(nrs)]   # listed syscall: jump to deny
    ins += [(0x06, 0, 0, 0x7FFF0000), (0x06, 0, 0, 0x00050000 | errno.EPERM)]
    return ins


def seccomp_program(machine, allow=()):
    """The blocklist as a raw BPF program (bytes), or None on an architecture without a list."""
    if machine not in ARCHES: return None
    blocks = [(ARCHES[machine][0], arch_block(blocked_numbers(machine, allow), SOCKET_NR[machine], True))]
    if machine == "x86_64": blocks.append((I386_AUDIT, arch_block(blocked_numbers_i386(allow), I386_SOCKET_NR, False)))
    deny = 0x00050000 | errno.EPERM
    ins, start = [(0x20, 0, 0, 4)], len(blocks) + 2             # the blocks begin after the arch checks and the deny
    for i, (audit, blk) in enumerate(blocks):
        ins += [(0x15, start - len(ins) - 1, 0, audit)]
        start += len(blk)
    ins += [(0x06, 0, 0, deny)]                                 # any other ABI: deny
    for _, blk in blocks: ins += blk
    return b"".join(struct.pack("=HBBI", *i) for i in ins)


def seccomp_file(allow=()):
    prog = seccomp_program(platform.machine(), allow)
    if prog is None: return None
    tf = tempfile.TemporaryFile()
    tf.write(prog); tf.flush(); tf.seek(0)
    return tf
