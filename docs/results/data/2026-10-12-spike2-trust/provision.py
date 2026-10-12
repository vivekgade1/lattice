# ruff: noqa
"""A module's 'provision step' that misbehaves: each attempt prints ALLOWED or BLOCKED."""
import os, socket, subprocess, sys, json
here = os.path.dirname(os.path.abspath(__file__))
outside = sys.argv[1]
results = {}
def attempt(name, fn):
    try:
        fn(); results[name] = "ALLOWED"
    except Exception as e:  # noqa
        results[name] = f"BLOCKED ({type(e).__name__}: {str(e)[:60]})"
def write(p):
    with open(p, "w") as f: f.write("x")
def tcp(host, port):
    s = socket.create_connection((host, port), timeout=3); s.close()
def udp_dns():
    socket.getaddrinfo("example.com", 443)
def unix(path):
    s = socket.socket(socket.AF_UNIX); s.settimeout(3); s.connect(path); s.close()
def listen():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1); s.close()
def read_home():
    open(os.path.expanduser("~/.zshrc")).read(1)
def child_curl():
    cmd = (["/usr/bin/curl", "-sS", "-m", "3", "-o", "/dev/null", "https://example.com"] if os.path.exists("/usr/bin/curl")
           else ["wget", "-q", "-T", "3", "-O", "/dev/null", "http://example.com"])
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0: raise OSError(r.stderr.decode().strip()[:60])
def child_write():
    r = subprocess.run(["/bin/sh", "-c", f"echo x > '{outside}/child.txt'"], capture_output=True)
    if r.returncode != 0: raise OSError(r.stderr.decode().strip()[:60])
attempt("write inside module dir", lambda: write(os.path.join(here, "state.txt")))
attempt("write outside (sibling dir)", lambda: write(os.path.join(outside, "escape.txt")))
attempt("write $TMPDIR", lambda: write(os.path.join(os.environ.get("TMPDIR", "/tmp"), "lattice-mod-trust-probe.txt")))
attempt("read ~/.zshrc (a home file)", read_home)
attempt("DNS lookup example.com", udp_dns)
attempt("TCP connect 1.1.1.1:443", lambda: tcp("1.1.1.1", 443))
attempt("TCP connect 127.0.0.1:" + sys.argv[2], lambda: tcp("127.0.0.1", int(sys.argv[2])))
attempt("unix socket docker.sock", lambda: unix(os.path.expanduser("~/.docker/run/docker.sock")) if os.path.exists(os.path.expanduser("~/.docker/run/docker.sock")) else unix("/var/run/docker.sock"))
attempt("listen on 127.0.0.1", listen)
attempt("child process: curl https", child_curl)
attempt("child process: write outside", child_write)
print(json.dumps(results, indent=1))
