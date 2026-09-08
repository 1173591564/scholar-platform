.PHONY: help install-dev init-scholar test-scholar check-proxy-backend check-proxy-frontend check

PYTHON ?= python
PROXY_BACKEND := services/proxy-hub/backend
PROXY_FRONTEND := services/proxy-hub/frontend

help:
	@printf '%s\n' \
		'install-dev          Install all development dependencies' \
		'init-scholar         Initialize Scholar runtime directories' \
		'test-scholar         Run the Scholar test suite' \
		'check-proxy-backend  Run Proxy Hub backend checks' \
		'check-proxy-frontend Run Proxy Hub frontend checks' \
		'check                Run all repository checks'

install-dev:
	$(PYTHON) -m pip install --upgrade 'pip>=24,<26' 'setuptools>=68'
	$(PYTHON) -m pip install -e '.[dev]'
	$(PYTHON) -m pip install -e '$(PROXY_BACKEND)[dev]'
	cd $(PROXY_FRONTEND) && npm ci

init-scholar:
	$(PYTHON) -m scholar init

test-scholar: init-scholar
	$(PYTHON) -m pytest tests -v --tb=short

check-proxy-backend:
	cd $(PROXY_BACKEND) && ruff check .
	cd $(PROXY_BACKEND) && ruff format --check .
	cd $(PROXY_BACKEND) && mypy proxy_hub
	cd $(PROXY_BACKEND) && pytest

check-proxy-frontend:
	cd $(PROXY_FRONTEND) && npm test
	cd $(PROXY_FRONTEND) && npm run typecheck
	cd $(PROXY_FRONTEND) && npm run build
	cd $(PROXY_FRONTEND) && npm audit --audit-level=high

check: test-scholar check-proxy-backend check-proxy-frontend
