.PHONY: up up-routing down logs test reset osrm

.env:
	cp .env.example .env

up: .env          ## start db, api, jobs and web
	docker compose up --build

up-routing: .env  ## same, plus the OSRM routing server
	docker compose --profile routing up --build

down:
	docker compose down

logs:
	docker compose logs -f api jobs

test:             ## run the scoring tests (no database needed)
	cd api && python -m pytest -q

reset:            ## wipe dispatches, audits and statuses, reload sample hospitals
	docker compose exec api python -m app.seed --reset

osrm:             ## build routing data for Massachusetts (one time)
	bash scripts/prepare-osrm.sh
