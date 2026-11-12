.PHONY: build check check-plugin reference-check

build:
	python3 tools/build.py

check:
	python3 -m compileall -q app tools tests
	python3 -m unittest discover -s tests -p 'test_*.py'

check-plugin:
	python3 tests/check_plugin.py

reference-check:
	python3 tools/research/validate-stack.py
