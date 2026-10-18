#!/bin/sh
# The declared stub generator (studio.toml [grpc] generate): proto/ -> gen/ (buf, protoc-gen-go v1.36.5,
# protoc-gen-go-grpc v1.5.1 from $TOY_TOOLS_BIN). gen/ is never committed and never edited by hand.
set -e
cd "$(dirname "$0")"
if [ -n "${TOY_TOOLS_BIN:-}" ]; then PATH="$TOY_TOOLS_BIN:$PATH"; export PATH; fi
rm -rf gen
buf generate
