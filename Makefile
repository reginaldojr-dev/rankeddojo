.PHONY: all

all:
	python -m pip install -e .
	python scripts/prepare_linux.py --run
