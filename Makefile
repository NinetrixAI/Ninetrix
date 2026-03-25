.PHONY: help build dev release-cli install lint

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies (Python + JS)
	cd packages/cli && pip install -e .
	cd packages/api && pip install -e .
	cd packages/mcp-gateway && pip install -e .
	cd packages/mcp-worker && pip install -e .
	pnpm install

build: ## Build all packages (runs turbo build → copies dashboard into api)
	turbo run build
	cp -r packages/dashboard/out/ packages/api/static/dashboard/

dev: ## Start the local stack (alias for ninetrix dev)
	cd packages/cli && ninetrix dev

lint: ## Lint all packages
	turbo run lint

release-cli: ## Build and publish CLI to PyPI
	cd packages/cli && python -m build && twine upload dist/*

release-api: ## Build and push API Docker image
	docker buildx build --platform linux/amd64,linux/arm64 \
		-t ghcr.io/ninetrixai/ninetrix-api:latest \
		-f packages/api/Dockerfile packages/api --push

release-mcp-gateway: ## Build and push MCP Gateway Docker image
	docker buildx build --platform linux/amd64,linux/arm64 \
		-t ghcr.io/ninetrixai/ninetrix-mcp-gateway:latest \
		-f packages/mcp-gateway/Dockerfile packages/mcp-gateway --push

release-mcp-worker: ## Build and push MCP Worker Docker image
	docker buildx build --platform linux/amd64,linux/arm64 \
		-t ghcr.io/ninetrixai/ninetrix-mcp-worker:latest \
		-f packages/mcp-worker/Dockerfile packages/mcp-worker --push
