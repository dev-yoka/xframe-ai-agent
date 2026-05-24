.PHONY: sync-contracts verify-contracts

sync-contracts:
	./scripts/sync_contracts.sh

verify-contracts:
	./scripts/sync_contracts.sh --check
