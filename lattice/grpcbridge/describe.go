package main

import (
	"context"
	"embed"
	"encoding/base64"
	"fmt"
	"path/filepath"
	"sort"
	"strings"

	"github.com/bufbuild/protocompile"
	"github.com/bufbuild/protocompile/linker"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protodesc"
	"google.golang.org/protobuf/reflect/protoreflect"
	"google.golang.org/protobuf/types/descriptorpb"
)

// stubs are the imports a service commonly names and a project need not vendor: a file on
// the include path wins over these.
//
//go:embed stubs
var stubFS embed.FS

type stubResolver struct{}

func (stubResolver) FindFileByPath(path string) (protocompile.SearchResult, error) {
	f, err := stubFS.Open("stubs/" + path)
	if err != nil {
		return protocompile.SearchResult{}, protoregNotFound(path)
	}
	return protocompile.SearchResult{Source: f}, nil
}

type notFound string

func (n notFound) Error() string { return "file does not exist: " + string(n) }

func protoregNotFound(p string) error { return notFound(p) }

// compile reads the files (paths relative to one of the include roots, or absolute).
func (b *bridge) compile(files, roots []string) (linker.Files, error) {
	abs := make([]string, 0, len(roots))
	for _, r := range roots {
		a, err := filepath.Abs(r)
		if err != nil {
			return nil, err
		}
		abs = append(abs, a)
	}
	var rel []string
	for _, f := range files {
		if filepath.IsAbs(f) {
			best := ""
			for _, r := range abs {
				if p, err := filepath.Rel(r, f); err == nil && !strings.HasPrefix(p, "..") && len(r) >= len(best) {
					best = r
				}
			}
			if best == "" {
				return nil, fmt.Errorf("%s is under no include root", f)
			}
			p, _ := filepath.Rel(best, f)
			rel = append(rel, filepath.ToSlash(p))
			continue
		}
		rel = append(rel, filepath.ToSlash(f))
	}
	comp := protocompile.Compiler{
		Resolver: protocompile.WithStandardImports(protocompile.CompositeResolver{
			&protocompile.SourceResolver{ImportPaths: abs},
			stubResolver{},
		}),
		SourceInfoMode: protocompile.SourceInfoStandard,
	}
	return comp.Compile(context.Background(), rel...)
}

func (b *bridge) describe(req *request) any {
	res, err := b.compile(req.Files, req.ProtoPaths)
	if err != nil {
		return errAnswer("proto_compile", err.Error())
	}
	reg := b.files
	if reg == nil {
		reg = map[string]protoreflect.FileDescriptor{}
	}
	protos := []any{}
	services := []any{}
	messages := []any{}
	enums := []any{}
	set := &descriptorpb.FileDescriptorSet{}
	seen := map[string]bool{}
	var addFile func(fd protoreflect.FileDescriptor)
	addFile = func(fd protoreflect.FileDescriptor) {
		if seen[fd.Path()] {
			return
		}
		seen[fd.Path()] = true
		imps := fd.Imports()
		for i := 0; i < imps.Len(); i++ {
			addFile(imps.Get(i).FileDescriptor)
		}
		set.File = append(set.File, canonicalFile(res, fd))
	}
	for _, f := range res {
		fd := f.(protoreflect.FileDescriptor)
		reg[fd.Path()] = fd
		addFile(fd)
		imports := []string{}
		for i := 0; i < fd.Imports().Len(); i++ {
			imports = append(imports, fd.Imports().Get(i).Path())
		}
		sort.Strings(imports)
		gopkg := ""
		if opts, ok := fd.Options().(*descriptorpb.FileOptions); ok && opts != nil {
			gopkg = opts.GetGoPackage()
		}
		protos = append(protos, map[string]any{
			"file": fd.Path(), "package": string(fd.Package()), "go_package": gopkg, "imports": imports,
		})
		for i := 0; i < fd.Services().Len(); i++ {
			services = append(services, describeService(fd, fd.Services().Get(i)))
		}
		collectMessages(fd.Messages(), &messages, &enums)
		for i := 0; i < fd.Enums().Len(); i++ {
			enums = append(enums, describeEnum(fd.Enums().Get(i)))
		}
	}
	b.files = reg
	raw, _ := proto.MarshalOptions{Deterministic: true}.Marshal(set)
	return map[string]any{
		"ok": true,
		"grpc": map[string]any{
			"protos": protos, "services": services, "messages": messages, "enums": enums,
		},
		"descriptor_set": base64.StdEncoding.EncodeToString(raw),
	}
}

// canonicalFile is the file's descriptor with its custom options parsed as known extensions, so
// that serialising it writes their fields in field-number order (protocompile keeps an option's
// fields in the order its interpreter met them, which is not stable between runs).
func canonicalFile(res linker.Files, fd protoreflect.FileDescriptor) *descriptorpb.FileDescriptorProto {
	fdp := protodesc.ToFileDescriptorProto(fd)
	raw, err := proto.MarshalOptions{Deterministic: true}.Marshal(fdp)
	if err != nil {
		return fdp
	}
	var out descriptorpb.FileDescriptorProto
	if err := (proto.UnmarshalOptions{Resolver: res.AsResolver()}).Unmarshal(raw, &out); err != nil {
		return fdp
	}
	return &out
}

func describeEnum(e protoreflect.EnumDescriptor) map[string]any {
	vals := []any{}
	for i := 0; i < e.Values().Len(); i++ {
		v := e.Values().Get(i)
		vals = append(vals, map[string]any{"name": string(v.Name()), "number": int(v.Number())})
	}
	return map[string]any{"name": string(e.FullName()), "values": vals}
}

func describeService(fd protoreflect.FileDescriptor, s protoreflect.ServiceDescriptor) map[string]any {
	rpcs := []any{}
	for i := 0; i < s.Methods().Len(); i++ {
		m := s.Methods().Get(i)
		r := map[string]any{
			"name": string(m.Name()), "input": string(m.Input().FullName()),
			"output":           string(m.Output().FullName()),
			"client_streaming": m.IsStreamingClient(), "server_streaming": m.IsStreamingServer(),
		}
		if h := httpRule(m); h != nil {
			r["http"] = h
		}
		rpcs = append(rpcs, r)
	}
	return map[string]any{"name": string(s.FullName()), "file": fd.Path(), "rpcs": rpcs}
}

// httpRule reads the (google.api.http) option as the unknown-field bytes protocompile keeps
// it in, decoded against the HttpRule layout (get=2 put=3 post=4 delete=5 patch=6 body=7).
func httpRule(m protoreflect.MethodDescriptor) map[string]any {
	opts, ok := m.Options().(*descriptorpb.MethodOptions)
	if !ok || opts == nil {
		return nil
	}
	raw, err := proto.MarshalOptions{Deterministic: true}.Marshal(opts)
	if err != nil {
		return nil
	}
	// field 72295728, wire type 2
	const tag = uint64(72295728)<<3 | 2
	for len(raw) > 0 {
		t, n := consumeVarint(raw)
		if n <= 0 {
			return nil
		}
		raw = raw[n:]
		wt := t & 7
		switch wt {
		case 2:
			l, n := consumeVarint(raw)
			if n <= 0 || int(l) > len(raw)-n {
				return nil
			}
			body := raw[n : n+int(l)]
			raw = raw[n+int(l):]
			if t == tag {
				return decodeRule(body)
			}
		case 0:
			_, n := consumeVarint(raw)
			if n <= 0 {
				return nil
			}
			raw = raw[n:]
		default:
			return nil
		}
	}
	return nil
}

func decodeRule(b []byte) map[string]any {
	out := map[string]any{"method": "", "path": "", "body": ""}
	names := map[uint64]string{2: "get", 3: "put", 4: "post", 5: "delete", 6: "patch"}
	for len(b) > 0 {
		t, n := consumeVarint(b)
		if n <= 0 {
			return out
		}
		b = b[n:]
		if t&7 != 2 {
			return out
		}
		l, n := consumeVarint(b)
		if n <= 0 || int(l) > len(b)-n {
			return out
		}
		v := string(b[n : n+int(l)])
		b = b[n+int(l):]
		if name, ok := names[t>>3]; ok {
			out["method"], out["path"] = name, v
		} else if t>>3 == 7 {
			out["body"] = v
		}
	}
	return out
}

func consumeVarint(b []byte) (uint64, int) {
	var v uint64
	for i := 0; i < len(b) && i < 10; i++ {
		v |= uint64(b[i]&0x7f) << (7 * uint(i))
		if b[i] < 0x80 {
			return v, i + 1
		}
	}
	return 0, -1
}

func collectMessages(ms protoreflect.MessageDescriptors, out *[]any, enums *[]any) {
	for i := 0; i < ms.Len(); i++ {
		m := ms.Get(i)
		if m.IsMapEntry() {
			continue
		}
		*out = append(*out, describeMessage(m))
		collectMessages(m.Messages(), out, enums)
		for j := 0; j < m.Enums().Len(); j++ {
			*enums = append(*enums, describeEnum(m.Enums().Get(j)))
		}
	}
}

func scalarName(f protoreflect.FieldDescriptor) string {
	switch f.Kind() {
	case protoreflect.MessageKind, protoreflect.GroupKind:
		return string(f.Message().FullName())
	case protoreflect.EnumKind:
		return string(f.Enum().FullName())
	}
	return f.Kind().String()
}

func describeMessage(m protoreflect.MessageDescriptor) map[string]any {
	fields := []any{}
	for i := 0; i < m.Fields().Len(); i++ {
		f := m.Fields().Get(i)
		d := map[string]any{"name": string(f.Name()), "number": int(f.Number())}
		switch {
		case f.IsMap():
			d["type"], d["label"] = "map", "map"
			d["key"], d["value"] = scalarName(f.MapKey()), scalarName(f.MapValue())
		case f.IsList():
			d["type"], d["label"] = scalarName(f), "repeated"
		case f.HasOptionalKeyword():
			d["type"], d["label"] = scalarName(f), "optional"
		default:
			d["type"], d["label"] = scalarName(f), "single"
		}
		if o := f.ContainingOneof(); o != nil && !o.IsSynthetic() {
			d["oneof"] = string(o.Name())
		}
		fields = append(fields, d)
	}
	return map[string]any{"name": string(m.FullName()), "fields": fields}
}
