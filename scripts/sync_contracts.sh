#!/usr/bin/env bash
# Sync workflow contracts from PriceFRAME and regenerate Pydantic models.
# Usage:
#   scripts/sync_contracts.sh
#   scripts/sync_contracts.sh --check

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SHARED_DIR="${PRICEFRAME_SHARED_DIR:-$REPO_ROOT/../PriceFRAME/shared}"
SOURCE_DIR="$SHARED_DIR/dist/contracts"
DEST_DIR="$REPO_ROOT/src/xframe_agent/workflows/contracts"
MODELS_DIR="$REPO_ROOT/src/xframe_agent/workflows/models"

mode="${1:-sync}"

if [[ "$mode" != "sync" && "$mode" != "--check" ]]; then
  echo "usage: scripts/sync_contracts.sh [--check]" >&2
  exit 2
fi

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "shared contracts not built. Run 'npm run build:contracts' in $SHARED_DIR first." >&2
  exit 2
fi

SHAPE="$SOURCE_DIR/workflow-contract.schema.json"
if [[ ! -f "$SHAPE" ]]; then
  echo "workflow-contract.schema.json missing in $SOURCE_DIR" >&2
  exit 2
fi

mkdir -p "$DEST_DIR" "$MODELS_DIR"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

for src in "$SOURCE_DIR"/*.json; do
  name="$(basename "$src" .json)"
  py_name="${name//./_}"
  cp "$src" "$tmpdir/${py_name}.json"
done

uv run datamodel-codegen \
  --input "$SHAPE" \
  --input-file-type jsonschema \
  --output "$tmpdir/contract_models.py" \
  --target-python-version 3.12 \
  --output-model-type pydantic_v2.BaseModel \
  --class-name WorkflowContract \
  --disable-timestamp \
  --field-constraints \
  --formatters ruff-format ruff-check \
  --use-standard-collections \
  --use-union-operator \
  --collapse-root-models

{
  cat <<'EOF'
# AUTO-GENERATED - do not edit by hand.
# ruff: noqa: N815
# Source: PriceFRAME/shared/dist/contracts/workflow-contract.schema.json
# Regenerate via: scripts/sync_contracts.sh
EOF
  cat "$tmpdir/contract_models.py"
} > "$tmpdir/create_pricing_request_v1.py"

uv run ruff check --fix --select I "$tmpdir/create_pricing_request_v1.py" >/dev/null

if [[ "$mode" == "--check" ]]; then
  ok=0
  expected_json_manifest="$tmpdir/expected-json.txt"
  actual_json_manifest="$tmpdir/actual-json.txt"
  expected_model_manifest="$tmpdir/expected-models.txt"
  actual_model_manifest="$tmpdir/actual-models.txt"

  find "$tmpdir" -maxdepth 1 -type f -name "*.json" -exec basename {} \; | sort > "$expected_json_manifest"
  find "$DEST_DIR" -maxdepth 1 -type f -name "*.json" -exec basename {} \; | sort > "$actual_json_manifest"
  printf '%s\n' "create_pricing_request_v1.py" > "$expected_model_manifest"
  find "$MODELS_DIR" -maxdepth 1 -type f -name "*.py" ! -name "__init__.py" -exec basename {} \; | sort > "$actual_model_manifest"

  if ! diff -q "$expected_json_manifest" "$actual_json_manifest" >/dev/null 2>&1; then
    comm -23 "$expected_json_manifest" "$actual_json_manifest" | sed "s/^/drift: missing /"
    comm -13 "$expected_json_manifest" "$actual_json_manifest" | sed "s/^/drift: stale /"
    ok=1
  fi

  if ! diff -q "$expected_model_manifest" "$actual_model_manifest" >/dev/null 2>&1; then
    comm -23 "$expected_model_manifest" "$actual_model_manifest" | sed "s/^/drift: missing /"
    comm -13 "$expected_model_manifest" "$actual_model_manifest" | sed "s/^/drift: stale /"
    ok=1
  fi

  for src in "$tmpdir"/*.json; do
    name="$(basename "$src")"
    if ! diff -q "$src" "$DEST_DIR/$name" >/dev/null 2>&1; then
      echo "drift: $name differs"
      ok=1
    fi
  done

  if ! diff -q "$tmpdir/create_pricing_request_v1.py" "$MODELS_DIR/create_pricing_request_v1.py" >/dev/null 2>&1; then
    echo "drift: create_pricing_request_v1.py differs"
    ok=1
  fi

  if [[ "$ok" -ne 0 ]]; then
    echo "Run scripts/sync_contracts.sh to update." >&2
    exit 1
  fi

  echo "contracts in sync"
  exit 0
fi

find "$DEST_DIR" -maxdepth 1 -type f -name "*.json" -delete
find "$MODELS_DIR" -maxdepth 1 -type f -name "*.py" ! -name "__init__.py" -delete

cp "$tmpdir"/*.json "$DEST_DIR/"
cp "$tmpdir/create_pricing_request_v1.py" "$MODELS_DIR/create_pricing_request_v1.py"
echo "synced contracts to $DEST_DIR and $MODELS_DIR"
