package svc

import (
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// internalErr is every database failure's answer.
func internalErr(err error) error {
	return status.Error(codes.Internal, "internal error: "+err.Error())
}
