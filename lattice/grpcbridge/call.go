package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net"
	"strings"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/reflect/protoreflect"
	"google.golang.org/protobuf/types/dynamicpb"
	"google.golang.org/protobuf/types/known/emptypb"
)

const defaultTimeout = 10 * time.Second

type bridge struct {
	files map[string]protoreflect.FileDescriptor
	conns map[string]*grpc.ClientConn
}

func newBridge() *bridge {
	return &bridge{files: map[string]protoreflect.FileDescriptor{}, conns: map[string]*grpc.ClientConn{}}
}

func (b *bridge) close() {
	for _, c := range b.conns {
		_ = c.Close()
	}
}

func (b *bridge) handle(req *request) any {
	switch req.Op {
	case "describe":
		return b.describe(req)
	case "call":
		return b.call(req)
	case "health":
		return b.health(req)
	case "probe":
		return b.probe(req)
	}
	return errAnswer("bad_request", "unknown op "+req.Op)
}

// conn dials a loopback target with insecure credentials (nothing else is ever dialled).
func (b *bridge) conn(target string) (*grpc.ClientConn, error) {
	host, _, err := net.SplitHostPort(target)
	if err != nil {
		return nil, fmt.Errorf("target %q is not host:port", target)
	}
	if host != "127.0.0.1" && host != "::1" {
		return nil, errNonLoopback(target)
	}
	if c, ok := b.conns[target]; ok {
		return c, nil
	}
	c, err := grpc.NewClient(target, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	b.conns[target] = c
	return c, nil
}

type nonLoopback string

func (n nonLoopback) Error() string { return "target " + string(n) + " is not a loopback address" }

func errNonLoopback(t string) error { return nonLoopback(t) }

func ctxFor(req *request) (context.Context, context.CancelFunc) {
	d := defaultTimeout
	if req.TimeoutMS > 0 {
		d = time.Duration(req.TimeoutMS) * time.Millisecond
	}
	return context.WithTimeout(context.Background(), d)
}

func (b *bridge) health(req *request) any {
	c, err := b.conn(req.Target)
	if err != nil {
		if _, ok := err.(nonLoopback); ok {
			return errAnswer("non_loopback", err.Error())
		}
		return errAnswer("dial", err.Error())
	}
	ctx, cancel := ctxFor(req)
	defer cancel()
	resp, err := healthpb.NewHealthClient(c).Check(ctx, &healthpb.HealthCheckRequest{})
	if err != nil {
		st, _ := status.FromError(err)
		return map[string]any{"ok": true, "serving": false, "code": codeName(st.Code()), "message": st.Message()}
	}
	return map[string]any{"ok": true, "serving": resp.GetStatus() == healthpb.HealthCheckResponse_SERVING,
		"status": resp.GetStatus().String()}
}

// probe calls a method by its wire path with an empty message, whether or not any described
// proto declares it (reconcile asks a server about an rpc its contract does not have).
func (b *bridge) probe(req *request) any {
	c, err := b.conn(req.Target)
	if err != nil {
		if _, ok := err.(nonLoopback); ok {
			return errAnswer("non_loopback", err.Error())
		}
		return errAnswer("dial", err.Error())
	}
	ctx, cancel := ctxFor(req)
	defer cancel()
	if err := c.Invoke(ctx, req.Path, &emptypb.Empty{}, &emptypb.Empty{}); err != nil {
		st, _ := status.FromError(err)
		return map[string]any{"ok": true, "code": codeName(st.Code()), "code_num": int(st.Code()), "message": st.Message()}
	}
	return map[string]any{"ok": true, "code": "OK", "code_num": 0, "message": ""}
}

func (b *bridge) method(service, method string) (protoreflect.MethodDescriptor, error) {
	for _, fd := range b.files {
		sd := fd.Services().ByName(protoreflect.Name(shortName(service)))
		if sd == nil || string(sd.FullName()) != service {
			continue
		}
		md := sd.Methods().ByName(protoreflect.Name(method))
		if md == nil {
			return nil, fmt.Errorf("service %s has no method %s", service, method)
		}
		return md, nil
	}
	return nil, fmt.Errorf("no service %s in the described files", service)
}

func shortName(full string) string {
	if i := strings.LastIndexByte(full, '.'); i >= 0 {
		return full[i+1:]
	}
	return full
}

func (b *bridge) call(req *request) any {
	md, err := b.method(req.Service, req.Method)
	if err != nil {
		return errAnswer("unknown_method", err.Error())
	}
	if md.IsStreamingClient() || md.IsStreamingServer() {
		return errAnswer("streaming_not_supported", req.Service+"/"+req.Method+" is a streaming method")
	}
	c, err := b.conn(req.Target)
	if err != nil {
		if _, ok := err.(nonLoopback); ok {
			return errAnswer("non_loopback", err.Error())
		}
		return errAnswer("dial", err.Error())
	}
	in := dynamicpb.NewMessage(md.Input())
	raw := bytes.TrimSpace(req.Message)
	if len(raw) == 0 || string(raw) == "null" {
		raw = []byte("{}")
	}
	if err := (protojson.UnmarshalOptions{}).Unmarshal(raw, in); err != nil {
		return errAnswer("bad_message", err.Error())
	}
	out := dynamicpb.NewMessage(md.Output())
	ctx, cancel := ctxFor(req)
	defer cancel()
	if len(req.Metadata) > 0 {
		pairs := make([]string, 0, 2*len(req.Metadata))
		for k, v := range req.Metadata {
			pairs = append(pairs, strings.ToLower(k), v)
		}
		ctx = metadata.AppendToOutgoingContext(ctx, pairs...)
	}
	path := "/" + string(md.Parent().FullName()) + "/" + string(md.Name())
	if err := c.Invoke(ctx, path, in, out); err != nil {
		st, _ := status.FromError(err)
		return map[string]any{"ok": true, "code": codeName(st.Code()), "code_num": int(st.Code()),
			"message": st.Message(), "body": nil}
	}
	body, err := canonical(out)
	if err != nil {
		return errAnswer("encode", err.Error())
	}
	return map[string]any{"ok": true, "code": "OK", "code_num": 0, "message": "", "body": body}
}

// ---- lattice.grpcjson/1 ----------------------------------------------------------------

const maxSafe = int64(1) << 53

// canonical is the message as a JSON value: proto field names, unpopulated fields emitted
// (message and optional fields as null, oneof members only when set), enums by name, and
// 64-bit integers within +/-2^53 as numbers (outside, as decimal strings).
func canonical(m protoreflect.Message) (any, error) {
	md := m.Descriptor()
	if strings.HasPrefix(string(md.FullName()), "google.protobuf.") {
		raw, err := protojson.MarshalOptions{UseProtoNames: true, EmitUnpopulated: true}.Marshal(m.Interface())
		if err != nil {
			return nil, err
		}
		return decodeJSON(raw)
	}
	out := map[string]any{}
	fs := md.Fields()
	for i := 0; i < fs.Len(); i++ {
		f := fs.Get(i)
		name := string(f.Name())
		inOneof := f.ContainingOneof() != nil && !f.ContainingOneof().IsSynthetic()
		switch {
		case f.IsMap():
			mv := map[string]any{}
			var rerr error
			m.Get(f).Map().Range(func(k protoreflect.MapKey, v protoreflect.Value) bool {
				val, err := value(f.MapValue(), v)
				if err != nil {
					rerr = err
					return false
				}
				mv[k.String()] = val
				return true
			})
			if rerr != nil {
				return nil, rerr
			}
			out[name] = mv
		case f.IsList():
			l := m.Get(f).List()
			arr := make([]any, 0, l.Len())
			for j := 0; j < l.Len(); j++ {
				val, err := value(f, l.Get(j))
				if err != nil {
					return nil, err
				}
				arr = append(arr, val)
			}
			out[name] = arr
		case !m.Has(f) && inOneof:
			continue
		case !m.Has(f) && (f.HasPresence()):
			out[name] = nil
		default:
			val, err := value(f, m.Get(f))
			if err != nil {
				return nil, err
			}
			out[name] = val
		}
	}
	return out, nil
}

func decodeJSON(raw []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	return v, nil
}

func value(f protoreflect.FieldDescriptor, v protoreflect.Value) (any, error) {
	switch f.Kind() {
	case protoreflect.BoolKind:
		return v.Bool(), nil
	case protoreflect.Int32Kind, protoreflect.Sint32Kind, protoreflect.Sfixed32Kind:
		return v.Int(), nil
	case protoreflect.Uint32Kind, protoreflect.Fixed32Kind:
		return v.Uint(), nil
	case protoreflect.Int64Kind, protoreflect.Sint64Kind, protoreflect.Sfixed64Kind:
		n := v.Int()
		if n >= -maxSafe && n <= maxSafe {
			return json.Number(fmt.Sprint(n)), nil
		}
		return fmt.Sprint(n), nil
	case protoreflect.Uint64Kind, protoreflect.Fixed64Kind:
		n := v.Uint()
		if n <= uint64(maxSafe) {
			return json.Number(fmt.Sprint(n)), nil
		}
		return fmt.Sprint(n), nil
	case protoreflect.FloatKind, protoreflect.DoubleKind:
		x := v.Float()
		switch {
		case math.IsNaN(x):
			return "NaN", nil
		case math.IsInf(x, 1):
			return "Infinity", nil
		case math.IsInf(x, -1):
			return "-Infinity", nil
		}
		return x, nil
	case protoreflect.StringKind:
		return v.String(), nil
	case protoreflect.BytesKind:
		return v.Bytes(), nil // encoding/json writes standard base64
	case protoreflect.EnumKind:
		if ev := f.Enum().Values().ByNumber(v.Enum()); ev != nil {
			return string(ev.Name()), nil
		}
		return int(v.Enum()), nil
	case protoreflect.MessageKind, protoreflect.GroupKind:
		return canonical(v.Message())
	}
	return nil, fmt.Errorf("unsupported kind %s", f.Kind())
}

// codeNames are google.rpc.Code's canonical names.
var codeNames = [...]string{
	"OK", "CANCELLED", "UNKNOWN", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED", "NOT_FOUND",
	"ALREADY_EXISTS", "PERMISSION_DENIED", "RESOURCE_EXHAUSTED", "FAILED_PRECONDITION",
	"ABORTED", "OUT_OF_RANGE", "UNIMPLEMENTED", "INTERNAL", "UNAVAILABLE", "DATA_LOSS",
	"UNAUTHENTICATED",
}

func codeName(c codes.Code) string {
	if int(c) < len(codeNames) {
		return codeNames[c]
	}
	return fmt.Sprintf("CODE_%d", uint32(c))
}
