PYTEST := .venv/bin/pytest

.PHONY: test

test:
	$(PYTEST) backend/tests -q
