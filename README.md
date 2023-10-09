# ocaml-scripts

Experimental scripts to build a BUCK/TARGETS file from an opam switch.
Forwords: this is Python, for now, because we hope to reuse most of this experiment to
improve supercaml in tp2, which requires python.

Ultimately we want to reuse these to build setup ocaml projects at Meta, and
to support buck2/ocaml for OSS.

*Disclaimer: rules.py is hardcoded to my (vsiles) current setup, please update it
to fit yours until we make a proper reusable script ;)*

Running `python3 meta2json.py` will dump all the collected information for the current
opam switch in the env into a .json file (data.json is provided as an example).
Running `python3 rules.py` will read the previously generated json file and
create a BUCK file aimed at OSS released.

These scripts are provided for internal experimentation only and should not be
used until we settle on a proper workflow.

*Note: rules.py might generate warnings like the following ones, which are
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
