# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import argparse
import json
import logging
import os
import sys
from typing import List


# All path in here are "local" to the opam root directory.
# We currently prepend them with 'opam' that can be a directory on disk,
# or a symlink. This is fully configurable
class Rules:
    output_file: str
    prefix: str
    prelude: str

    def add_prefix(self, name):
        if name is None:
            return name
        return os.path.join(self.prefix, name)

    def map_prefix(self, lst):
        return [os.path.join(self.prefix, name) for name in lst]

    def __init__(self, output_file, prefix) -> None:
        self.output_file = output_file
        self.prefix = prefix
        self.prelude = "# buildifier: disable=no-effect"

        # Reset the file to an empty file
        with open(self.output_file, "w"):
            pass

    def _open(self, fp, indent, name) -> None:
        s = " " * indent
        fp.write("{}{}\n".format(s, self.prelude))
        fp.write("{}{}(\n".format(s, name))

    def _close(self, fp, indent) -> None:
        s = " " * indent
        fp.write("{}) if not host_info().os.is_windows else None\n\n".format(s))

    def _writeln(self, fp, indent, line):
        s = " " * indent
        fp.write("{}{}\n".format(s, line))

    def _writeln_list(self, fp, indent, name, lst):
        self._writeln(fp, indent, "{} = [".format(name))
        for item in lst:
            self._writeln(fp, indent + 4, '"{}",'.format(item))
        self._writeln(fp, indent, "],")

    def command_alias(self, name: str, exe: str, resources: List[str]) -> None:
        # TODO(shaynefletcher)
        # Find a way to compute default_target_platform
        visibility = ["PUBLIC"]
        with open(self.output_file, "a") as fp:
            self._open(fp, 0, "command_alias")
            self._writeln(fp, 4, 'name = "{}",'.format(name))
            self._writeln(fp, 4, 'exe = "{}",'.format(exe))
            self._writeln_list(fp, 4, "resources", resources)
            self._writeln_list(fp, 4, "visibility", visibility)
            self._close(fp, 0)

    def sh_binary(self, name: str, main: str) -> None:
        # TODO(shaynefletcher)
        # Find a way to compute default_host_platform and default_target_platform
        visibility = ["PUBLIC"]
        main = self.add_prefix(main)
        with open(self.output_file, "a") as fp:
            self._open(fp, 0, "sh_binary")
            self._writeln(fp, 4, 'name = "{}",'.format(name))
            self._writeln(fp, 4, 'main = "{}",'.format(main))
            self._writeln_list(fp, 4, "visibility", visibility)
            self._close(fp, 0)

    def prebuilt_cxx_library(
        self, name: str, header_dirs: List[str], header_only: bool
    ) -> None:
        # TODO: we do not add prefix for now because we pass it only
        # symlink/special names
        visibility = ["PUBLIC"]
        with open(self.output_file, "a") as fp:
            self._open(fp, 0, "prebuilt_cxx_library")
            self._writeln(fp, 4, 'name = "{}",'.format(name))
            self._writeln_list(fp, 4, "header_dirs", header_dirs)
            self._writeln(
                fp, 4, "header_only = {},".format("True" if header_only else "False")
            )
            self._writeln_list(fp, 4, "visibility", visibility)
            self._close(fp, 0)

    def export_file(self, name: str, src: str) -> None:
        src = self.add_prefix(src)
        with open(self.output_file, "a") as fp:
            self._open(fp, 0, "export_file")
            self._writeln(fp, 4, 'name = "{}",'.format(name))
            self._writeln(fp, 4, 'src = "{}",'.format(src))
            self._close(fp, 0)

    # TODO:
    # We don't take visibility, lib_name, lib_dir nor c_libs as input because
    # we never had to until now. Revisit the decision if necessary.
    def prebuilt_ocaml_library(
        self,
        name: str,
        native: bool = False,
        include_dir: str = None,
        native_lib: str = None,
        bytecode_lib: str = None,
        native_c_libs: List[str] = (),
        bytecode_c_libs: List[str] = (),
        deps: List[str] = (),
    ) -> None:
        # Default values expected by prebuilt_ocaml_library that we don't really
        # know what to do with at the moment.
        visibility = ["PUBLIC"]
        lib_name = name
        lib_dir = ""
        bytecode_only = (not native,)

        include_dir = self.add_prefix(include_dir)
        native_c_libs = self.map_prefix(native_c_libs)
        bytecode_c_libs = self.map_prefix(bytecode_c_libs)
        bytecode_lib = self.add_prefix(bytecode_lib)
        native_lib = self.add_prefix(native_lib)
        deps = [":{}".format(dep) for dep in deps]

        with open(self.output_file, "a") as fp:
            self._open(fp, 0, "prebuilt_ocaml_library")
            self._writeln(fp, 4, 'name = "{}",'.format(name))
            self._writeln_list(fp, 4, "visibility", visibility)
            self._writeln(fp, 4, 'lib_name = "{}",'.format(lib_name))
            self._writeln(fp, 4, 'lib_dir = "{}",'.format(lib_dir))
            self._writeln(fp, 4, 'include_dir = "{}",'.format(include_dir))
            if native_lib is not None:
                self._writeln(fp, 4, 'native_lib = "{}",'.format(native_lib))
            if bytecode_lib is not None:
                self._writeln(fp, 4, 'bytecode_lib = "{}",'.format(bytecode_lib))
            self._writeln(fp, 4, "c_libs = None,")
            self._writeln_list(fp, 4, "native_c_libs", native_c_libs)
            self._writeln_list(fp, 4, "bytecode_c_libs", bytecode_c_libs)
            self._writeln(
                fp,
                4,
                "bytecode_only = {},".format("True" if bytecode_only else "False"),
            )
            self._writeln_list(fp, 4, "deps", deps)
            self._close(fp, 0)


# In the json blob, directory paths are usually absolute.
# We might end up with not the same prefix than the build
# directory. Therefore we hack a bit and read the path from right to left
# until we reach the switch name (and remove it), which leaves
# paths like `bin/foo.exe` or `lib/pkgname/...`
def get_lib_local_path(path, switch_name):
    local_path = []
    basename = ""
    while not basename == switch_name:
        (path, basename) = os.path.split(path)
        local_path.append(basename)
    # drop last (which will be the switch_name) and rebuild the path
    del local_path[-1]
    local_path.reverse()
    return os.path.join(*local_path)


# Check that the requested files are there on disk before registering a
# dependency
def check_file(build, target_dir, filename):
    path = os.path.normpath(os.path.join(target_dir, filename))
    realpath = os.path.normpath(os.path.join(build, path))
    if not os.path.exists(realpath):
        logging.warning("Could not find file: " + realpath)
        return None
    else:
        return path


def check_archives(build, target_dir, archives):
    return [
        os.path.join(target_dir, a)
        for a in archives
        if check_file(build, target_dir, a)
    ]


# Process a single json entry. Most if it is automatic. However
# we don't have a clear source of truth for the dependencies to non ocaml
# libraries, like system threads or pcre, ...
# This part requires a bit of ad-hoc support still
def _process(rules, opam_switch, pkg):
    switch_name = os.path.basename(opam_switch)
    relativelib = "lib"
    logging.info("processing package: {}".format(pkg))
    name = pkg["name"]
    directory = pkg["directory"]
    target_dir = get_lib_local_path(directory, switch_name)

    # we don't use the dynamic part of the archives yet, because we don't
    # load libraries dynamically
    static_byte_libs = pkg["static_byte_libs"]
    static_native_libs = pkg["static_native_libs"]
    # dyn_byte_libs = pkg['dyn_byte_libs']
    dyn_native_libs = pkg["dyn_native_libs"]

    # native means "not bytecode_only"
    native = False
    if len(static_native_libs) + len(dyn_native_libs) > 0:
        native = True

    # check if the library is present on disk as it might be
    # an external dependency like libpng or z
    # vsiles:
    # In <= 4.14.0, the content of several libraries, including
    # threads/posix, unix, and runtime_events, are not correctly
    # installed: all the cm* files are in ocaml/<lib>/ but the
    # lib*.a files are in ocaml/ directly, so we need to patch
    # that manually.
    ocaml = os.path.join(relativelib, "ocaml")
    compiler_nativelibs = [
        "camlruntime_eventsnat",
        "unixnat",
        "threadsnat",
        "camlstrnat",
    ]
    compiler_bytelibs = [
        "camlruntime_eventsbyt",
        "unixbyt",
        "threads",
        "camlstrbyt",
    ]

    native_c_libs = []
    for lib in pkg["native_c_libs"]:
        lib_name = "lib{}.a".format(lib)
        lib_path = check_file(opam_switch, target_dir, lib_name)
        if not lib_path and lib in compiler_nativelibs:
            # e.g. lib/ocaml/libunixnat.a
            lib_path = check_file(opam_switch, ocaml, lib_name)
        if lib_path:
            native_c_libs.append(lib_path)

    bytecode_c_libs = []
    for lib in pkg["bytecode_c_libs"]:
        lib_name = "lib{}.a".format(lib)
        lib_path = check_file(opam_switch, target_dir, lib_name)
        if not lib_path and lib in compiler_bytelibs:
            # e.g. 'lib/ocaml/libunixbyt.a'
            lib_path = check_file(opam_switch, ocaml, lib_name)
        if lib_path:
            bytecode_c_libs.append(lib_path)

    static_byte_libs = check_archives(opam_switch, target_dir, static_byte_libs)
    static_native_libs = check_archives(opam_switch, target_dir, static_native_libs)

    dependencies = pkg["dependencies"]

    native_c_lib_paths = pkg["native_c_lib_paths"]
    bytecode_c_lib_paths = pkg["bytecode_c_lib_paths"]

    c_lib_paths = native_c_lib_paths + bytecode_c_lib_paths

    # Here we log the info we got from ocamlobjinfo, just to help debugging
    # and improving the support for external libraries
    for c_lib_path in set(c_lib_paths):
        logging.info("VS: {} c_lib_path {}".format(name, c_lib_path))

    native_lib = None
    if len(static_native_libs) == 1:
        native_lib = static_native_libs[0]
    elif len(static_native_libs) > 1:
        logging.error("Too many native libs for package {}.".format(name))
        sys.exit(1)

    bytecode_lib = None
    if len(static_byte_libs) == 1:
        bytecode_lib = static_byte_libs[0]
    elif len(static_byte_libs) > 1:
        logging.error("Too many bytecode libs for package {}.".format(name))
        sys.exit(1)

    new_rules = []
    new_rules.append(
        rules.prebuilt_ocaml_library(
            name=name,
            native=native,
            include_dir=target_dir,
            native_lib=native_lib,
            bytecode_lib=bytecode_lib,
            native_c_libs=native_c_libs,
            bytecode_c_libs=bytecode_c_libs,
            deps=list(set(dependencies)),  # remove dups
        )
    )

    # If the pkg was a ppx_deriver or rewriter, it might have some
    # ppx_runtime_deps. We'll create a secondary target, empty but for
    # these dependencies
    if "ppx_runtime_deps" in pkg:
        ppx_runtime_deps = pkg["ppx_runtime_deps"]
        new_rules.append(
            rules.prebuilt_ocaml_library(
                name="{}-runtime-deps".format(name),
                include_dir=target_dir,
                bytecode_lib=None,
                native_lib=None,
                deps=ppx_runtime_deps,
            )
        )

    # TODO(vsiles): until we have this alternative for PPXs, we still need
    # the good old cmxs export
    dyn_native_libs = pkg["dyn_native_libs"]
    for dyn_lib in dyn_native_libs:
        if dyn_lib.endswith(".cmxs"):
            short_dyn_lib = dyn_lib[:-5]
            new_rules.append(
                rules.export_file(
                    name="{}.{}-plugin".format(name, short_dyn_lib),
                    src=os.path.join(target_dir, dyn_lib),
                )
            )

    # Now, let's export the js_of_ocaml runtime information
    if "jsoo_runtime" in pkg:
        jsoo_runtime = pkg["jsoo_runtime"]
        for entry in jsoo_runtime:
            new_rules.append(
                rules.export_file(
                    name="{}.{}".format(name, entry),
                    src=os.path.join(target_dir, entry),
                )
            )

    return new_rules


# Process the result of meta2json and export targets for buck.
# Most of it should be automated, by processing the json
# file. However we still add a few dependencies by hand, for anything
# depending on system libraries, like pcre, libev, ...
def process_json(rules, json_path, opam_switch):
    with open(json_path) as fp:
        json_data = json.load(fp)

    meta_rules = []
    for pkgname in json_data:
        pkg = json_data[pkgname]
        new_rules = _process(rules, opam_switch, pkg)
        meta_rules.extend(new_rules)
    return meta_rules


def gen_targets(rules, json_path, local_root, opam_switch):
    rules_list = []

    relativelib = "lib"
    relativebin = "bin"

    ocaml = os.path.join(relativelib, "ocaml")

    rules_list.append(
        rules.prebuilt_cxx_library(
            name="ocaml-dev",
            header_dirs=[rules.add_prefix(ocaml)],
            header_only=True,
        )
    )

    rules_list.append(
        rules.export_file(name="libasmrun.a", src=os.path.join(ocaml, "libasmrun.a"))
    )

    logging.info("Emitting 'interop_includes'")
    rules_list.append(
        rules.export_file(
            name="interop_includes", src=os.path.join(relativelib, "ocaml")
        )
    )

    # Support calling ocamldebug like e.g.
    #  `buck2 run fbcode//third-party-buck/platform010/build/supercaml:ocamldebug-exe`
    # by using a command_alias for it.
    rules_list.append(
        rules.export_file(name="ocamlrun", src=os.path.join(relativebin, "ocamlrun"))
    )
    rules_list.append(
        rules.export_file(
            name="ocamldebug", src=os.path.join(relativebin, "ocamldebug")
        )
    )
    rules_list.append(
        rules.command_alias(
            name="ocamldebug-exe",
            exe=":ocamldebug",
            # RC: I do not know what `ROOT/lib/ocaml` is supposed to do here.
            # It only works, if `local_root` is a symlink!
            resources=[
                ":ocamlrun",
                ":ocamldebug",
                os.path.join(local_root, ocaml),
            ],
        )
    )

    # TODO: fix
    logging.info("Emitting binaries")
    bin = os.path.join(opam_switch, relativebin)
    for b in os.listdir(bin):
        b_path = os.path.join(bin, b)

        if (
            not b.endswith((".byte", ".native", ".exe"))
            and not os.path.islink(b_path)
            and not os.path.exists(b_path + ".opt")
        ):
            name, _ = os.path.splitext(b)
            if name == "ocamldebug":
                # Special case, we declare a command_alias to correctly invoke
                # it using ocamlrun
                continue
            if name == "ocamlformat.parser_recovery":
                # two exe break the format pattern by having
                # test_gen and test_driver as extensions, with the same
                # `name`
                rules_list.append(
                    rules.sh_binary(
                        name="%s-exe" % b, main=os.path.join(relativebin, b)
                    )
                )
            else:
                rules_list.append(
                    rules.sh_binary(
                        name="%s-exe" % name, main=os.path.join(relativebin, b)
                    )
                )

            if name == "js_of_ocaml":
                rules_list.append(
                    rules.export_file(
                        name="%s-runtime.js" % name,
                        src=os.path.join(
                            relativelib, "js_of_ocaml-compiler", "runtime.js"
                        ),
                    )
                )
        if b.endswith(".byte"):
            rules_list.append(
                rules.export_file(name=b, src=os.path.join(relativebin, b))
            )

    logging.info("Emitting library rules based on {}".format(json_path))
    meta_rules = process_json(rules, json_path, opam_switch)

    logging.info("Finished generating TARGETS")
    return rules_list + meta_rules


def check_argument(parser, name, param):
    if param is None:
        print("Missing argument '{}'".format(name))
        parser.print_help()
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="rules.py",
        description="Process raw json information generated by meta2json.py into buck files",
    )
    parser.add_argument("-i", "--input", help="Input json file. Mandatory.")
    parser.add_argument("-o", "--output", help="Output buck file name. Mandatory.")

    # Something like "OPAM_SWITCH_PREFIX=/Users/$USER/.opam/default"
    # this is usually set by running `eval $(opam env)` in your shell
    parser.add_argument(
        "-s",
        "--switch",
        help="Absolute path to the opam switch used by meta2json.py. Mandatory.",
    )
    # In the generated file, this will be the root of the relative path,
    # like in `native_lib = "opam/lib/astring/astring_top.cmxa"`
    #                        ^^^^ that's the root
    # Default value is `opam`
    parser.add_argument(
        "-r",
        "--root",
        help="relative path used as a root for all paths in the generated file. Optional.",
    )

    args = parser.parse_args()

    input_file = args.input
    output_file = args.output
    opam_switch = args.switch
    local_root = "opam"
    if args.root is not None:
        logging.info("overriding default 'opam' root with {}".format(args.root))
        local_root = args.root

    check_argument(parser, "-i", input_file)
    check_argument(parser, "-o", output_file)
    check_argument(parser, "-s", opam_switch)

    rules = Rules(output_file, local_root)
    gen_targets(rules, input_file, local_root, opam_switch)


if __name__ == "__main__":
    main()
