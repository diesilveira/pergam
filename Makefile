.PHONY: up down restart logs ps health clean psql post-example

up:
	docker compose up -d --build

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

health:
	@curl -sS http://localhost:1111/healthz && echo

# WARNING: stops everything AND wipes the bundled Postgres volume (all grids).
clean:
	docker compose down -v

# Open a psql shell on the bundled database.
psql:
	docker compose exec db psql -U pergam -d pergam

# Publish a tiny example grid (assumes the bundled service is up on localhost:1111).
post-example:
	@python3 -c "import json,pathlib; pathlib.Path('/tmp/pergam-example.json').write_text(json.dumps({'title':'Example','html':'<!doctype html><html><head><title>Example</title></head><body style=\"font-family:sans-serif;background:#0d1117;color:#e6edf3;padding:2rem\"><h1 style=\"color:#58a6ff\">Hello, pergam</h1><p>Posted from the Makefile.</p></body></html>','grid_type':'otro','author':'you@example.com'}))"
	@curl -sS -X POST http://localhost:1111/grid -H 'Content-Type: application/json' --data-binary @/tmp/pergam-example.json
	@echo
