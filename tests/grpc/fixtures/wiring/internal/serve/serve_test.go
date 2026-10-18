package serve

import (
	"testing"

	"google.golang.org/grpc"
)

func TestGone(t *testing.T) {
	registerGone(grpc.NewServer())
}
