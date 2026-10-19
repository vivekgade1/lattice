// Package app assembles the server.
package app

import (
	"context"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	d5 "example.com/d5main/gen/descwrap5/v1"
)

// PtrDesc hands the generated descriptor out by pointer.
func PtrDesc() *grpc.ServiceDesc { return &d5.Ptr_ServiceDesc }

// CopyDesc returns a modified value copy of the generated descriptor.
func CopyDesc() *grpc.ServiceDesc {
	d := d5.Copy_ServiceDesc
	d.Metadata = "copy"
	return &d
}

// AliasDesc re-serves the Template service's methods under another service name.
func AliasDesc() *grpc.ServiceDesc {
	d := d5.Template_ServiceDesc
	d.ServiceName = "descwrap5.v1.Alias"
	return &d
}

type ptrImpl struct{ d5.UnimplementedPtrServer }

func (*ptrImpl) Get(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return nil, status.Error(codes.NotFound, "no ptr")
}

func (*ptrImpl) Put(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

type copyImpl struct{ d5.UnimplementedCopyServer }

func (*copyImpl) Get(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return nil, status.Error(codes.InvalidArgument, "bad copy")
}

func (*copyImpl) Put(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

type aliasImpl struct{ d5.UnimplementedAliasServer }

func (*aliasImpl) Get(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return nil, status.Error(codes.AlreadyExists, "dup alias")
}

func (*aliasImpl) Put(ctx context.Context, in *d5.Req) (*d5.Rep, error) {
	return &d5.Rep{Name: in.Name}, nil
}

// Build assembles the server.
func Build() *grpc.Server {
	s := grpc.NewServer()
	s.RegisterService(PtrDesc(), &ptrImpl{})
	s.RegisterService(CopyDesc(), &copyImpl{})
	s.RegisterService(AliasDesc(), &aliasImpl{})
	return s
}
