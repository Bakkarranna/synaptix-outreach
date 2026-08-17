.DEFAULT_GOAL := help
.PHONY: help logs test docker-test stop build up install setup run admin daemon web package check

help:
	@perl -nle'print $& if m{^[a-zA-Z_-]+:.*?## .*$$}' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

install: ## install all Python dependencies (local dev)
	pip install uv 2>/dev/null || true
	uv pip install -r requirements/local.txt

setup: install ## install deps + migrate + bootstrap CRM
	python manage.py migrate --no-input
	python manage.py setup_crm

run: ## run the daemon in the foreground
	python manage.py rundaemon

daemon: run ## alias for run

admin: web

web: ## start the Console web server (dashboard + Django Admin)
	@echo ""
	@echo "  Synaptix Studio Console: http://localhost:8000/dashboard/"
	@echo "  No staff account yet? Run: python manage.py createsuperuser"
	@echo ""
	python manage.py runserver

console: web ## start the Console web server

check: ## run Django system checks
	python manage.py check

test: ## run the test suite
	.venv/bin/pytest

package: ## build a distributable bundle (scripts/package.ps1 / package.sh)
	bash scripts/package.sh

# Docker targets
logs: ## follow the logs of the service
	docker compose -f local.yml logs -f

docker-test: ## run tests in Docker
	docker compose -f local.yml run --remove-orphans app py.test -vv -p no:cacheprovider

stop: ## stop all services defined in Docker Compose
	docker compose -f local.yml stop

build: ## build all services defined in Docker Compose
	docker compose -f local.yml build

up: ## run the defined service in Docker Compose
	docker compose -f local.yml up --build -d
	docker compose -f local.yml logs -f
