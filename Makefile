short_ver = 0.9.0
long_ver = $(shell git describe --long 2>/dev/null || echo $(short_ver)-0-unknown-g`git describe --always`)
generated = aiven/client/version.py
PYTHON ?= python

all: $(generated)

aiven/client/version.py: .git/index
	echo "__version__ = '$(long_ver)'" > $@

test: flake8 pylint pytest

flake8:
	$(PYTHON) -m flake8.main --max-line-length=125 aiven/ tests/

pylint:
	$(PYTHON) -m pylint aiven/ --rcfile pylintrc tests/

pytest:
	$(PYTHON) -m pytest -vv tests/

clean:
	$(RM) -r rpms

rpm: $(generated)
	git archive --prefix=aiven-client/ HEAD -o rpm-src-aiven-client.tar
	# add generated files to the tar, they're not in git repository
	tar -r -f rpm-src-aiven-client.tar --transform=s,aiven/,aiven-client/aiven/, $(generated)
	rpmbuild -bb aiven-client.spec \
		--define '_sourcedir $(CURDIR)' \
		--define '_rpmdir $(PWD)/rpms' \
		--define 'major_version $(short_ver)' \
		--define 'minor_version $(subst -,.,$(subst $(short_ver)-,,$(long_ver)))'
	$(RM) rpm-src-aiven-client.tar
