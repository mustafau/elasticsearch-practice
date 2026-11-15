PYTHON ?= python3
POSTMAN ?= postman
export ES_URL ?= http://localhost:9200
SCENARIO_ARG = $(if $(SCENARIO),--scenario $(SCENARIO),)

.PHONY: up down bootstrap seed setup-all status reset check tooling-test postman-lint postman-test test hybrid-demo
up:
	docker compose up -d elasticsearch
	$(PYTHON) tools/lab.py wait
down:
	docker compose down
bootstrap seed status reset:
	$(PYTHON) tools/lab.py $@ $(SCENARIO_ARG)
setup-all:
	$(PYTHON) tools/lab.py setup-all
check:
	$(PYTHON) tools/lab.py check
tooling-test:
	$(PYTHON) -m unittest discover -s tools/tests -v
postman-lint:
	$(POSTMAN) collection lint postman/collections/elasticsearch-practice
postman-test:
	$(PYTHON) tools/lab.py postman-test --postman $(POSTMAN) $(SCENARIO_ARG)
test: check tooling-test postman-lint
	$(PYTHON) tools/lab.py test --postman $(POSTMAN)
hybrid-demo:
	$(PYTHON) tools/lab.py hybrid-demo
