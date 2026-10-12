# M-S: can a module's provision step run with no network and only declared paths writable? (macOS)

Date: 2026-10-12. Spike 2 (`docs/design/spikes/02-module-marketplace.md`, phase M-S). A measurement, not a
build: nothing in Lattice's core sandboxes module code (decision 5 of the brief). Machine: macOS 27.0
(build 26A428), Apple silicon, Docker 29.4.0 (Docker Desktop), Python 3.14 from
`/Library/Frameworks/Python.framework`. Raw artefacts: `docs/results/data/2026-10-12-spike2-trust/`
(the probe script and the three profiles, with the scratch path replaced by `<probe>` and the home
directory by `<home>`).

## Question

A community module (the plan's untrusted tier) brings a *provision step*: a subprocess Lattice would run
to set the module up. Can that subprocess be confined on this machine to (a) no network and (b) writes
only under the module's declared directory, with what holes, at what cost?

## Method

`provision.py` is a misbehaving provision step. Each attempt prints ALLOWED or BLOCKED (with the error):
write inside its own directory (declared), write into a sibling directory (`outside/`), write `$TMPDIR`,
read a home file (`~/.zshrc`), DNS lookup, TCP to 1.1.1.1:443, TCP to a listening server on 127.0.0.1
(a `python3 -m http.server` started for the probe), connect to the Docker socket
(`~/.docker/run/docker.sock`), listen on 127.0.0.1, a child process doing HTTPS (`curl`, or busybox
`wget` in the container), a child process writing outside.

Four runs, the same script:

1. unsandboxed (control);
2. `sandbox-exec -f profile.sb`: `(allow default) (deny network*) (deny file-write*) (allow file-write*
   (subpath <probe>/module) (literal "/dev/null") (literal "/dev/dtracehelper"))`;
3. `sandbox-exec -f profile-strict.sb`: the same plus `(deny file-read* (subpath <home>)) (allow
   file-read* (subpath <probe>/module))`;
4. `docker run --rm --network none --read-only --tmpfs /tmp -v <probe>/module:/mod -v
   <probe>/outside:/outside:ro python:3.13-alpine python /mod/provision.py` (container
   `lattice-mod-trust-probe`).

Then two follow-ups: the Docker CLI under `profile.sb`, and a profile that allows only the Docker socket
(`profile-docker.sb`) running `docker run -v <probe>/outside:/o ... sh -c 'echo escaped >
/o/via-docker.txt'`. Cost: wall time of `python3 -c pass` plain, under each profile (20 runs each) and in
`docker run --rm --network none` (10 runs), measured with `time.perf_counter` around `subprocess.run`.

## Results

| Attempt | Unsandboxed | `sandbox-exec` profile.sb | `sandbox-exec` strict | `docker --network none` |
|---|---|---|---|---|
| write inside module dir | ALLOWED | ALLOWED | ALLOWED | ALLOWED (bind mount) |
| write sibling dir | ALLOWED | BLOCKED (EPERM) | BLOCKED (EPERM) | BLOCKED (read-only mount) |
| write `$TMPDIR` | ALLOWED | BLOCKED (EPERM) | BLOCKED (EPERM) | ALLOWED, but the container's own tmpfs (host untouched) |
| read `~/.zshrc` | ALLOWED | **ALLOWED** | BLOCKED (EPERM) | BLOCKED (not in the container) |
| DNS lookup | ALLOWED | BLOCKED | BLOCKED | BLOCKED |
| TCP 1.1.1.1:443 | ALLOWED | BLOCKED (EPERM) | BLOCKED (EPERM) | BLOCKED (network unreachable) |
| TCP 127.0.0.1 (host server) | ALLOWED | BLOCKED (EPERM) | BLOCKED (EPERM) | BLOCKED (the container's own loopback: refused) |
| unix socket `docker.sock` | ALLOWED | BLOCKED (EPERM) | BLOCKED (EPERM) | BLOCKED (not mounted) |
| listen on 127.0.0.1 | ALLOWED | BLOCKED (EPERM) | BLOCKED (EPERM) | ALLOWED (inside the container only) |
| child: HTTPS | ALLOWED | BLOCKED (cannot resolve) | BLOCKED | BLOCKED (bad address) |
| child: write outside | ALLOWED | BLOCKED (EPERM) | BLOCKED (EPERM) | BLOCKED (read-only) |

Follow-ups:

- `sandbox-exec -f profile.sb docker ps`: `permission denied while trying to connect to the docker API at
  unix:///Users/vivekgade/.docker/run/docker.sock`. **An engine's provision step that needs Docker cannot
  run under a no-network profile**: the Docker socket is a unix socket and `network*` covers it.
- With the socket allowed (`profile-docker.sb`), the sandboxed process wrote
  `<probe>/outside/via-docker.txt` ("escaped") through a container with a bind mount: **allowing the
  Docker socket gives the module the host** (any path can be mounted; the file sandbox does not apply to
  the Docker daemon). The file was removed after the check.

Cost (median of the runs; min-max):

| Variant | Median | Range | Overhead |
|---|---|---|---|
| `python3 -c pass` | 12.3 ms | 11.7-14.4 | - |
| under `sandbox-exec` profile.sb | 16.7 ms | 16.6-19.0 | +4.4 ms per process |
| under `sandbox-exec` strict | 16.8 ms | 16.3-17.6 | +4.5 ms |
| `docker run --rm --network none` | 134.3 ms | 126.3-142.0 | +122 ms per process (image already pulled) |

## What is enforced, what is not

Enforced by `sandbox-exec` with a deny-network, deny-write profile (all 11 attempts behaved as intended
except the read): every TCP/UDP/DNS connection and every unix-socket connection, listening, writes outside
the declared directory, all of it inherited by child processes. Errors are clean `EPERM`s, so a provision
step fails loudly rather than hanging.

Not enforced, or not by the default profile:

1. **Reads.** With `(allow default)` the step reads any file the user can (`~/.zshrc` ALLOWED; so
   `~/.ssh`, a project's `.env`). It cannot send them anywhere (no network), but it can copy them into its
   own directory, which Lattice later reads or the module ships. The strict profile blocks reads under the
   home directory, at the price of listing every path the step needs (a Python in a uv venv under the home
   directory would need its own allowance; this probe used the system framework Python).
2. **Docker.** An engine's provision is "start a container": that needs the Docker socket, and the socket
   is equivalent to host access. So the sandbox can confine a provision step that computes or writes
   files, not one that drives Docker. Lattice would have to start the container itself from the
   descriptor's data (as the built-in engines already do: the `[server]` block is data, no module code
   runs) and never hand the socket to module code.
3. **IPC.** `(allow default)` leaves Mach and XPC lookups open; a process can ask other system services
   to act for it outside the sandbox. Not exercised here (launching apps or launchd jobs is invasive);
   a deny-default profile would be needed and was not built.
4. **Environment variables** are inherited (an API key in the environment is visible to the step).
   Lattice would have to start the step with a scrubbed environment.
5. **`sandbox-exec` is deprecated** by Apple (no replacement CLI); it works on macOS 27.0 today, with no
   guarantee. Linux would need a different mechanism (namespaces, seccomp, or the container route).
6. **The adapter is not a provision step.** An engine module's `adapter = "module:Class"` is imported into
   the checker process (the one code hook built in this spike); the sandbox above does not apply to it.
   Only data-only descriptors (adapter = a built-in name) run no module code at all.

Docker `--network none` confines just as well for network and host writes (the host only sees the declared
bind mount), isolates reads completely (the container sees only what is mounted), costs about 122 ms more
per process, needs Docker running, and has the same Docker-socket limit (mounting the socket would undo
it).

## Answer

Yes for a provision step that computes and writes files: on this macOS machine `sandbox-exec` with a
deny-network, deny-file-write (plus a declared-directory allowance) profile enforced no network and
declared-paths-only writes for the step and its children, for about 4.5 ms per process; add a home-read
deny for confidentiality. `docker run --network none` is the portable equivalent at about 134 ms per
process. No for a provision step that needs Docker (every engine's): the Docker socket defeats the
sandbox, so engine provisioning has to stay data that Lattice executes (the descriptor's `[server]`
block), which is what the descriptor-only slice already does. Not measured: Mach/XPC escapes, Linux.
