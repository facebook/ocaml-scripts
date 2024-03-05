# ocaml-scripts [![use pre-existing switch](https://github.com/facebook/ocaml-scripts/actions/workflows/run-tests.yml/badge.svg)](https://github.com/facebook/ocaml-scripts/actions/workflows/run-tests.yml)[![build switch from json](https://github.com/shayne-fletcher/ocaml-scripts/actions/workflows/json.yml/badge.svg)](https://github.com/shayne-fletcher/ocaml-scripts/actions/workflows/json.yml)

Experimental scripts to build a BUCK/TARGETS file from an opam switch.
Forwords: this is Python, for now, because we hope to reuse most of this experiment to
improve supercaml in tp2, which requires python.

Ultimately we want to reuse these to setup ocaml projects at Meta, and
to support buck2/ocaml for OSS.

*Disclaimer: all scripts are a work in progress, please update them
to fit your needs and report your changes back.*

All scripts display a help screen showing all possible arguments by adding `-h` or `--help` to their command line.

`BUCK_PATH` usually is something like `./third-party/BUCK` or `./third-party/ocaml/BUCK` in Buck's root directory.

#### dromedary.py

You can use `dromedary.py` to generate an opam switch or use an existing one.

Running `python3 dromedary.py -o BUCK_PATH JSON_CONFIG` will use the JSON configuration file `JSON_CONFIG` to generate a new Opam switch and install the configured packages. The Buck 2 file `BUCK_PATH` is generated from the packages of the generated switch.

Running `python3 dromedary.py -s SWITCH_NAME -o BUCK_PATH` will parse the Opam switch `SWITCH_NAME` for package information and create a BUCK file at `BUCK_PATH`. If no `-s SWITCH_NAME` is given, the currently active one is used. To exclude packages from `BUCK_PATH`, use `-e` with a space separated list of package names: `python3 dromedary.py -s SWITCH_NAME -o BUCK_PATH -e ocaml_lsp_server ocamlformat` does not include the packages `ocaml_lsp_server` and `ocamlformat`.

JSON configuration:

See file [./dromedary_example.json](./dromedary_example.json).

```json
{
    "name": "./third-party",
    "compiler": "ocaml-base-compiler",
    "packages": [
        "menhirLib",
        "sedlex=3.2",
        "alcotest>=1.7.0"
    ]
}
```

- `name` - Either the path to the directory to generate the Opam switch in or the global name of the switch. The path is relative to the directory of the JSON file. If this is missing, `"./"` is used.
- `compiler` - The compiler to use for the switch. If this is missing, `"ocaml-variants"` is used.
- `packages` - An array of Opam packages to install, may contain version numbers, each string is passed verbatim to `opam install`. _Must_ be present.

**Note**: `dromedary.py` internally uses the scripts `meta2json.py` and `rules.py`, so no need to call them separately.

#### meta2json.py

Running `python3 meta2json.py -o JSON_PATH` or `python3 dromedary.py -j JSON_PATH -o BUCK_PATH` will dump all the collected information for the current opam switch in the env into a .json file `JSON_PATH` (data.json is provided as an example). To exclude packages from the generated file(s), use `-e` with a space separated list of package names: `python3 meta2json.py -o JSON_PATH -e ocaml_lsp_server ocamlformat` does not include the packages `ocaml_lsp_server` and `ocamlformat`.

#### rules.py

Running `python3 rules.py -i JSON_PATH -s SWITCH_ROOT_PATH -o BUCK_PATH` will read the previously generated json file `JSON_PATH` and
create a BUCK file `BUCK_PATH` aimed at OSS releases.

These scripts are provided for internal experimentation only and should not be
used until we settle on a proper workflow.

**Note**: `rules.py` and `dromedary.py` might generate warnings like the following ones, which are
expected: these are native libraries name that we try to locate in the opam
switch (and where we fail, because they are system wide libraries, not opam
ones).

```
WARNING:root:Could not find file: /data/users/vsiles/opam/4.14.0+flambda/lib/ctypes/libintegers_stubs.a
WARNING:root:Could not find file: /data/users/vsiles/opam/4.14.0+flambda/lib/mtime/clock/os/librt.a
...
```

## Join the ocaml-scripts community
See the [CONTRIBUTING](CONTRIBUTING.md) file for how to help out.

## License
ocaml-scripts is MIT licensed, as found in the LICENSE file.
