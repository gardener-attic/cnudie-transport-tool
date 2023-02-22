# Cnudie Transport Tool

## run script outside of docker
- install Python 3.10
- checkout repos to local machine
  - https://github.com/gardener/cnudie-transport-tool
  - https://github.com/gardener/cc-utils
  - https://github.com/gardener/component-spec
- set environment variables
  - `export CC_CONFIG_DIR=<path to cc-config repo>`
  - `export PYTHONPATH=<path to component-spec repo>/bindings-python:<path to cc-utils repo>:${PYTHONPATH}`
- start the tool via `python3 ./ctt.py <flags>`

the tool allows passing in the input component descriptor via 2 ways:

1. provide the address of a remote component descriptor via the flags

```
--src-ctx-repo-url
--component-name
--component-version
```

2. provide the path to a component descriptor that exists in a local yaml file via the flag

```
--component-descriptor
```

additional mandatory flags are `--processing-config` and `--tgt-ctx-repo-url`

for other flags, see the `main` function in `./ctt.py`
