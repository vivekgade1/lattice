// Package authn is the authentication gate.
package authn

import (
	"context"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protoreflect"
	"google.golang.org/protobuf/reflect/protoregistry"

	optionsv1 "example.com/a5opt/gen/audience5/options/v1"
)

// ownNamespace is the proto package prefix whose methods the gate governs.
const ownNamespace = "audience5."

// Gate refuses calls to member-audience methods that carry no token.
type Gate struct{}

// NewGate builds a Gate.
func NewGate() *Gate { return &Gate{} }

func bearer(ctx context.Context) string {
	md, _ := metadata.FromIncomingContext(ctx)
	for _, v := range md.Get("authorization") {
		if strings.HasPrefix(v, "Bearer ") {
			return strings.TrimPrefix(v, "Bearer ")
		}
	}
	return ""
}

// audience reads the method option for fullMethod ("/pkg.Service/Method").
func (g *Gate) audience(fullMethod string) optionsv1.Audience {
	name := strings.TrimPrefix(fullMethod, "/")
	aud := optionsv1.Audience_AUDIENCE_UNSPECIFIED
	if !strings.HasPrefix(name, ownNamespace) {
		aud = optionsv1.Audience_AUDIENCE_ANONYMOUS
	} else if d, err := protoregistry.GlobalFiles.FindDescriptorByName(
		protoreflect.FullName(strings.Replace(name, "/", ".", 1))); err == nil {
		if md, ok := d.(protoreflect.MethodDescriptor); ok && md.Options() != nil {
			if v, ok := proto.GetExtension(md.Options(), optionsv1.E_Audience).(optionsv1.Audience); ok {
				aud = v
			}
		}
	}
	return aud
}

// Unary is the unary half of the gate.
func (g *Gate) Unary() grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo, next grpc.UnaryHandler) (any, error) {
		if g.audience(info.FullMethod) == optionsv1.Audience_AUDIENCE_ANONYMOUS {
			return next(ctx, req)
		}
		if bearer(ctx) == "" {
			return nil, status.Error(codes.Unauthenticated, "missing token")
		}
		return next(ctx, req)
	}
}
