#!/bin/bash

python3-coverage run --branch -m unittest discover -s tests $1 && \
	python3-coverage report -m --include './*' && \
	python3 -m pep8 romlib/*.py
