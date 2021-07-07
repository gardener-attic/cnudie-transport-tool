# Cnudie Transport Tool

## build & run docker container
```
docker build -t cnudie-transport-tool
docker run cnudie-transport-tool python3 /cnudie-transport-tool/ctt/process_dependencies.py --help

docker run --env CC_CONFIG_DIR=/cc-config-slim -v /Users/i500806/dev/cc-config-slim:/cc-config-slim -v $(pwd)/.vscode/test-cd.yaml:/test-cd.yaml  -v $(pwd)/.vscode/processing.cfg:/processing.cfg cnudie-transport-tool:0.0.1 python3 /cnudie-transport-tool/ctt/process_dependencies.py --tgt-ctx-repo-url <target context repo url> --component-descriptor /test-cd.yaml --processing-config /processing.cfg
```

## run script outside of docker
- install Python 3.9
- checkout repos to local machine
  - https://github.com/gardener/cnudie-transport-tool
  - https://github.com/gardener/cc-utils
  - https://github.com/gardener/component-spec
- set environment variables
  - `export CC_CONFIG_DIR=<path to cc-config repo>`
  - `export PYTHONPATH=<path to component-spec repo>/bindings-python:<path to cc-utils repo>:${PYTHONPATH}`
- start the tool via `python3 ./ctt/process_dependencies.py <flags>`

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

for other flags, see the `main` function in `./ctt/process_dependencies.py`
