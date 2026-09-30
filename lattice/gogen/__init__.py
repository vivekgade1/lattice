"""Go target: the Go runtime module (rt/, module lattice.dev/rt) and the Go code
generator (`t2.emit_go_t2`: T2 procedures from the T2 IR; `types`: T2 types in Go;
`endpoints.emit_go_endpoints`: endpoint plans as net/http handlers; `build.build_go_app`
and `python -m lattice.gogen build`: a checked API program as one Go binary).

Prerequisites for building (and for the tests marked `go`, which are skipped without
`go` on PATH):

* Go >= 1.26 on PATH. Builds run with GOTOOLCHAIN=local, so go never fetches a newer
  toolchain; an older one is refused with a GoBuildError naming the minimum.
* The runtime's dependencies in the Go module cache. Builds are offline (GOPROXY=off),
  so on a fresh machine run, once and with network access:

      cd lattice/gogen/rt && go mod download

  Without it a build fails with a GoBuildError saying so (go's own message is only
  "module lookup disabled by GOPROXY=off").
"""
