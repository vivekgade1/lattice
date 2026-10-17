// grpcbridge: the Lattice gRPC client, a long-lived process speaking JSON lines.
//
// One request per stdin line, one answer per stdout line:
//
//	{"op":"describe","files":[...],"proto_paths":[...]}  compile the .proto files (protocompile,
//	                                                      pure Go) and answer their contract
//	{"op":"call","target":"127.0.0.1:P","service":"pkg.Svc","method":"M","message":{...},
//	 "metadata":{"k":"v"}}                                one unary call
//	{"op":"health","target":"127.0.0.1:P"}                grpc.health.v1.Health/Check
//	{"op":"quit"}
//
// It dials only loopback addresses, with insecure credentials, and refuses streaming
// methods. Its JSON is canonical (sorted keys, no insignificant whitespace) and is the
// message form `lattice.grpcjson/1`: protojson with proto field names, unpopulated fields
// emitted, enums by name, and 64-bit integers within +/-2^53 as JSON numbers.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
)

type request struct {
	Op         string            `json:"op"`
	Files      []string          `json:"files"`
	ProtoPaths []string          `json:"proto_paths"`
	Target     string            `json:"target"`
	Path       string            `json:"path"`
	Service    string            `json:"service"`
	Method     string            `json:"method"`
	Message    json.RawMessage   `json:"message"`
	Metadata   map[string]string `json:"metadata"`
	TimeoutMS  int               `json:"timeout_ms"`
}

func main() {
	b := newBridge()
	defer b.close()
	in := bufio.NewReaderSize(os.Stdin, 1<<20)
	out := bufio.NewWriter(os.Stdout)
	enc := json.NewEncoder(out)
	enc.SetEscapeHTML(false)
	for {
		line, err := in.ReadBytes('\n')
		if len(line) > 0 {
			var req request
			var ans any
			if jerr := json.Unmarshal(line, &req); jerr != nil {
				ans = errAnswer("bad_request", jerr.Error())
			} else if req.Op == "quit" {
				_ = enc.Encode(map[string]any{"ok": true})
				_ = out.Flush()
				return
			} else {
				ans = b.handle(&req)
			}
			if eerr := enc.Encode(ans); eerr != nil {
				fmt.Fprintln(os.Stderr, "grpcbridge: cannot encode the answer:", eerr)
				return
			}
			_ = out.Flush()
		}
		if err != nil {
			return
		}
	}
}

func errAnswer(kind, msg string) map[string]any {
	return map[string]any{"ok": false, "error": kind, "message": msg}
}
