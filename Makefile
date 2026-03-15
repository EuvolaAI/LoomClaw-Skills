.PHONY: install eval

install:
	python -m pip install -e .[dev]

eval:
	python -m pytest evals -q
