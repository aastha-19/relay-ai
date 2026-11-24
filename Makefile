.PHONY: up down build test

up:
	docker-compose up --build -d || docker compose up --build -d

down:
	docker-compose down || docker compose down

build:
	docker-compose build || docker compose build

test:
	pytest backend/tests
