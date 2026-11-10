#| 50_wayland: SECURITY-CRITICAL: minimal Wayland client that registers a security context so the program gets a restricted socket.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- Wayland security context
class WlError(Exception):
    pass


def wl_string(s):
    b = s.encode() + b"\0"
    return struct.pack("=I", len(b)) + b + b"\0" * (-len(b) % 4)


class Wl:
    """A tiny Wayland client: just enough of the wire protocol to ask for a security context (no libwayland needed)."""
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(3)
        try: self.sock.connect(path)
        except OSError:
            self.sock.close(); raise
        self.last, self.buf = 1, b""              # object 1 is wl_display

    def new_id(self):
        self.last += 1
        return self.last

    def send(self, obj, opcode, payload=b"", fds=()):
        msg = struct.pack("=II", obj, ((8 + len(payload)) << 16) | opcode) + payload
        if fds: self.sock.sendmsg([msg], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))])
        else: self.sock.sendall(msg)

    def read(self, n):
        while len(self.buf) < n:
            d = self.sock.recv(4096)
            if not d: raise WlError("the compositor closed the connection")
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def roundtrip(self, registry):
        """wl_display.sync, then read events until it is answered. Returns the (name, interface, version) globals seen."""
        cb = self.new_id()
        self.send(1, 0, struct.pack("=I", cb))
        found = []
        while True:
            head = self.read(8)
            obj, word = struct.unpack("=II", head)
            data = self.read((word >> 16) - 8)
            op = word & 0xFFFF
            if obj == 1 and op == 0:              # wl_display.error(object, code, message)
                (n,) = struct.unpack_from("=I", data, 8)
                raise WlError(data[12:12 + n - 1].decode(errors="replace"))
            if obj == registry and op == 0:       # wl_registry.global(name, interface, version)
                name, n = struct.unpack_from("=II", data)
                iface = data[8:8 + n - 1].decode(errors="replace")
                (ver,) = struct.unpack_from("=I", data, 8 + (n + 3) // 4 * 4)
                found.append((name, iface, ver))
            elif obj == cb and op == 0:
                return found

    def close(self):
        try: self.sock.close()
        except OSError: pass


class WlContext:
    """What must stay open while the program runs: closing `hangup` tells the compositor to stop listening."""
    def __init__(self, hangup, path):
        self.hangup, self.path = hangup, path

    def close(self):
        try: os.close(self.hangup)
        except OSError: pass
        try: os.unlink(self.path)
        except OSError: pass


def wl_supported(display):
    """Does the compositor at this socket offer wp_security_context_manager_v1? (Read-only: nothing is registered.)"""
    try: c = Wl(display)
    except OSError: return False
    try:
        reg = c.new_id()
        c.send(1, 1, struct.pack("=I", reg))      # wl_display.get_registry
        return any(g[1] == "wp_security_context_manager_v1" for g in c.roundtrip(reg))
    except (OSError, WlError, struct.error):
        return False
    finally:
        c.close()


def wayland_context(display, sock_path, app_id, instance):
    """Register a listening socket with a security context. The program connects to that socket instead of the
    compositor's own, and the compositor then treats it as a sandboxed client (no screen capture, no input injection...).
    Returns a WlContext, or None when the compositor doesn't offer the protocol (or something went wrong; a warning says so)."""
    try: c = Wl(display)
    except OSError as e:
        warn(f"can't reach the Wayland compositor: {e.strerror}"); return None
    srv, pipe, made = None, None, False
    try:
        reg = c.new_id()
        c.send(1, 1, struct.pack("=I", reg))
        found = next((g for g in c.roundtrip(reg) if g[1] == "wp_security_context_manager_v1"), None)
        if not found: return None
        mgr = c.new_id()
        c.send(reg, 0, struct.pack("=I", found[0]) + wl_string(found[1]) + struct.pack("=II", 1, mgr))   # wl_registry.bind
        os.makedirs(os.path.dirname(sock_path), mode=0o700, exist_ok=True)
        try: os.unlink(sock_path)
        except FileNotFoundError: pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path); made = True
        srv.listen(32)
        pipe = os.pipe()
        ctx = c.new_id()
        c.send(mgr, 1, struct.pack("=I", ctx), fds=[srv.fileno(), pipe[0]])    # create_listener(id, listen_fd, close_fd)
        c.send(ctx, 1, wl_string("org.sbx"))                                   # set_sandbox_engine
        c.send(ctx, 2, wl_string(app_id))                                      # set_app_id
        c.send(ctx, 3, wl_string(instance))                                    # set_instance_id
        c.send(ctx, 4)                                                         # commit
        c.roundtrip(reg)                                                       # a protocol error shows up here
        os.close(pipe[0])
        out = WlContext(pipe[1], sock_path)
        pipe = None
        return out
    except (OSError, WlError, struct.error) as e:
        warn(f"Wayland security context failed: {e}")
        if made:
            try: os.unlink(sock_path)
            except OSError: pass
        return None
    finally:
        if srv: srv.close()
        if pipe:
            for fd in pipe: os.close(fd)
        c.close()
