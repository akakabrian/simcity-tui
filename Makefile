VENDOR := vendor/micropolis/MicropolisCore/src/MicropolisEngine
ENGINE := $(VENDOR)/objs/_micropolisengine.so
SRC    := $(wildcard $(VENDOR)/src/*.cpp)
PYINC  := $(shell python3-config --includes)

# macOS linker: Python C-API symbols (_Py_NoneStruct, PyArg_ParseTuple, …)
# aren't available at link time for extension modules; the host Python
# process provides them at load time. Without -undefined dynamic_lookup,
# ld64 rejects the build. Linux ld is lenient about undefined symbols in
# shared libs, so this flag is a no-op there.
LDFLAGS_EXT := $(if $(filter Darwin,$(shell uname -s)),-undefined dynamic_lookup,)

.PHONY: all bootstrap engine run clean venv test

# One-shot first-time setup for a fresh clone: fetch the Micropolis tree,
# build the SWIG module, create the venv. After this, `make run` works.
all: bootstrap engine venv

bootstrap: vendor/micropolis/.git
vendor/micropolis/.git:
	@echo "==> fetching SimHacker/micropolis into vendor/ (~153 MB, one time)"
	@mkdir -p vendor
	git clone --depth=1 https://github.com/SimHacker/micropolis.git vendor/micropolis
	@echo "==> patching SWIG glue for Python 3 (PyInt → PyLong, PyString → PyUnicode)"
	sed -i.bak \
	    -e 's/PyInt_FromLong/PyLong_FromLong/g' \
	    -e 's/PyString_FromString/PyUnicode_FromString/g' \
	    $(VENDOR)/swig/micropolisengine-swig-python.i
	@echo "==> bootstrap complete — run 'make engine' next"

engine: $(ENGINE)

$(ENGINE): $(VENDOR)/swig/micropolisengine.i $(SRC)
	mkdir -p $(VENDOR)/objs
	swig -c++ -python -I$(VENDOR)/src -I$(VENDOR)/swig \
		-o $(VENDOR)/objs/micropolisengine_wrap.cpp \
		-outdir $(VENDOR)/objs \
		$(VENDOR)/swig/micropolisengine.i
	g++ -shared -fPIC -O2 -w $(PYINC) $(LDFLAGS_EXT) -I$(VENDOR)/src \
		$(VENDOR)/objs/micropolisengine_wrap.cpp $(SRC) \
		-o $(ENGINE)

venv: .venv/bin/python
.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -e .

run: venv $(ENGINE)
	.venv/bin/python simcity.py

# Run all three test suites (TUI via Pilot, REST agent API, perf).
test: venv $(ENGINE)
	.venv/bin/python -m tests.qa
	.venv/bin/python -m tests.api_qa
	.venv/bin/python -m tests.perf

# Fast smoke test for trivial changes — just the sound diagnostic, runs
# in ~2 seconds. Use when you edited sounds.py or swapped an asset.
test-sound: venv
	.venv/bin/python -m tests.sound_test

# Subset of the main QA suite matching a pattern. Usage:
#   make test-only PAT=sound     # scenarios with "sound" in the name
#   make test-only PAT=cursor
test-only: venv $(ENGINE)
	.venv/bin/python -m tests.qa $(PAT)

clean:
	rm -rf $(VENDOR)/objs
