.PHONY: install api worker dev test sample clean

install:
	pip install -r requirements.txt

api:
	uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

worker:
	python -m api.worker

dev:
	@echo "abra dois terminais: 'make api' e 'make worker'"

sample:
	python tests/make_sample.py tests/fixtures

test:
	python -m pytest -q

clean:
	rm -rf out storage/jobs storage/clipforge.db*
