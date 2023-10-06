#!/usr/bin/env python3

# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# This script works with Python 3.8.18 and later.

"""
Script to parse an Opam sandbox and generate a `BUCK` containing its packages.

This is the combination of two existing scripts, `./meta2json.py` and
`./rules.py`.
"""

import argparse
from io import TextIOWrapper
import json
import logging
import os
import sys
import re
from typing import Any, Callable, Dict, List, Literal, Tuple, Union, cast
from subprocess import PIPE, Popen  # nosec

###############################################################################
# start of the first part, the contents of the file `./meta2json.py`.


# takes a list of strings, strips the leading/trailing spaces
# and remove the empty entries.
# We pass in an optional lambda to apply to non-empty entries
def clean_up(lst: List[str], f: Union[Callable[[str], str], None] = None) -> List[str]:
    for i, v in enumerate(lst):
        s = v.strip()
        if s != "" and f is not None:
            lst[i] = f(s)

    lst2 = list(filter(lambda s: s != "", lst))
    return lst2


# Let's fetch the lib*.a info from cmas and cmxas using ocamlobjinfo
# We can also extracts a few system libraries from this, which
# will reduce the ad-hoc bits in rules.py
#
# We are interested in the 'Extra C object files' entry of ocamlobjinfo
#
# Note for future me: if we ever need to load dynamic ocaml objects,
# there is an entry for this in the cm{x,}a
# Extra dynamically-loaded libraries:
def find_c_libs(full_path: str) -> Tuple[List[str], List[str]]:
    cmd = ["ocamlobjinfo", full_path]
    process = Popen(cmd, stdout=PIPE)  # nosec
    (output, _) = process.communicate()
    exit_code = process.wait()
    link_info: List[str] = []
    path_info: List[str] = []

    if exit_code != 0:
        print("[ocamlobjinfo] Can't read {}.".format(full_path))
        return (path_info, link_info)

    info = output.decode("UTF-8").splitlines()
    prefix = "Extra C object files: "
    for line in info:
        if line.startswith(prefix):
            line = line[len(prefix) :]
            # at this point we have a line with multiple -l and -L. Let's
            # split
            entries = line.strip().split()
            for entry in entries:
                if entry.startswith("-l"):
                    link_info.append(entry[2:])
                elif entry.startswith("-L"):
                    path_info.append(entry[2:])
                else:
                    print("While processing {}".format(full_path))
                    print("Unsupport Extra C object files: {}".format(entry))
    return (path_info, link_info)


def ocamlfind(cmd: List[str]) -> str:
    query = ["ocamlfind"] + cmd
    process = Popen(query, stdout=PIPE)  # nosec
    (output, _) = process.communicate()
    exit_code = process.wait()
    if exit_code != 0:
        print("Invalid cmd: {}".format(str(cmd)), file=sys.stderr)
        return ""
    return output.decode("UTF-8").strip()


def ocamlfind_query(query: List[str]) -> str:
    cmd = ["query"] + query
    return ocamlfind(cmd)


def ocamlfind_list() -> str:
    cmd = ["list"]
    return ocamlfind(cmd)


def package_name(libname: str) -> str:
    query = ["-format", "%p", libname]
    return ocamlfind_query(query)


def package_directory(libname: str) -> str:
    query = ["-format", "%d", libname]
    return ocamlfind_query(query)


# these paths are comma and whitespace separated
def process_paths(s: str) -> List[str]:
    lst = re.split(r"\s|,", s)
    lst2 = clean_up(lst, None)
    return lst2


# js runtime binaries
def jsoo_runtime(libname: str) -> List[str]:
    query = ["-format", "%(jsoo_runtime)", libname]
    s = ocamlfind_query(query).strip().split()
    return s


# predicates are comma-separated
def archive(libname: str, predicates: str) -> List[str]:
    if predicates == "":
        query = []
    else:
        query = ["-predicates", predicates]
    query = query + ["-format", "%a", libname]
    s = ocamlfind_query(query)
    lst = process_paths(s)
    return lst


def variable(
    libname: str, format_str: str, predicates: str, recursive: bool = False
) -> List[str]:
    query = ["-predicates", predicates, "-format", "%({})".format(format_str), libname]
    if recursive:
        query = ["-recursive"] + query
    s = ocamlfind_query(query)
    lst = process_paths(s)
    return lst


def plugin(libname: str, predicates: str) -> List[str]:
    return variable(libname, "plugin", predicates)


# opam packages can have optional dependencies, so sometimes a META
# file will reference a package that is not installed.
# For example, camlimages.all requires camlimages.graphics, which is
# not described.
#
# This function takes the list of all installed packages and use it
# to filter the results of dependencies to only existing ones.
def sanitize(pkg_list: List[str], lst: List[str]) -> List[str]:
    return list(filter(lambda s: s in pkg_list, lst))


def requires(pkg_list: List[str], libname: str, predicates: str) -> List[str]:
    lst = variable(libname, "requires", predicates)
    return sanitize(pkg_list, lst)


def ppx_runtime_deps(pkg_list: List[str], libname: str, predicates: str) -> List[str]:
    # vsiles:
    # We need `recursive` here because some ppx don't display any
    # runtime deps but their requirements do
    lst = variable(libname, "ppx_runtime_deps", predicates, recursive=True)
    return sanitize(pkg_list, lst)


# TODO(vsiles)
# I didn't find a way to output comments in the TARGETS file.
# We might want to add warning/error entries to ocaml_external_library
# Just to output this content somewhere (and discard it during the build)
def warning(libname: str, predicates: str) -> str:
    query = ["-format", "%(warning)", libname]
    if predicates != "":
        query = ["-predicates", predicates] + query
    return ocamlfind_query(query)


def error(libname: str, predicates: str) -> str:
    query = ["-format", "%(error)", libname]
    if predicates != "":
        query = ["-predicates", predicates] + query
    return ocamlfind_query(query)


PLAIN: Literal["PLAIN"] = "PLAIN"
REWRITER: Literal["REWRITER"] = "REWRITER"
DERIVER: Literal["DERIVER"] = "DERIVER"


def library_kind(libname: str) -> Literal["PLAIN", "REWRITER", "DERIVER"]:
    query = ["-format", "%(library_kind)", libname]
    kind = ocamlfind_query(query)
    if kind == "":
        return PLAIN
    elif kind == "ppx_rewriter":
        return REWRITER
    elif kind == "ppx_deriver":
        return DERIVER
    else:
        print("Invalid library_kind: {}".format(str(kind)), file=sys.stderr)
        sys.exit(1)


def package_list() -> List[str]:
    # the package list is of the form
    # pkg1 (version ...)
    # ...
    # pkgN (version ...)
    # so we split on ')' first, and then split on whitespace to keep
    # the first part (pkg_i)
    lst = ocamlfind_list().split(")")
    lst2 = clean_up(lst, lambda s: s.split()[0])
    return lst2


def add_predicate(p: str, newp: str) -> str:
    if p == "":
        return newp
    else:
        return "{},{}".format(p, newp)


# Here we assume we want the posix threads support, so we
# do our requests with mt,mt_posix.
# If that would ever change, we need to use mt,mt_vm
def process_lib(pkg_list: List[str], libname: str) -> Dict[str, Union[str, List[str]]]:
    data: Dict[str, Union[str, List[str]]] = {}
    data["name"] = package_name(libname)
    directory = package_directory(libname)
    data["directory"] = directory
    kind = library_kind(libname)
    data["kind"] = kind
    p0 = "mt,mt_posix"
    if kind == REWRITER or kind == DERIVER:
        p0 = add_predicate(p0, "ppx_driver")
        ppx_rt_deps = ppx_runtime_deps(pkg_list, libname, p0)
        # Always generate ppx_runtime_deps, it will be easier
        # for the bzl files later on
        # Because buck2 has recursive requirements, we just need
        # to get the runtime deps. Otherwise, we would need to add
        # their transitive requirements too
        data["ppx_runtime_deps"] = ppx_rt_deps
    pbyte = add_predicate(p0, "byte")
    pnative = add_predicate(p0, "native")

    # I could probably only read the "right" entry, but I'd like to be
    # resilient to buggy META files
    cmxa: List[str] = []
    cma: List[str] = []

    static_byte_libs = archive(libname, pbyte)
    cmxa.extend([lib for lib in static_byte_libs if lib.endswith(".cmxa")])
    cma.extend([lib for lib in static_byte_libs if lib.endswith(".cma")])

    static_native_libs = archive(libname, pnative)
    cmxa.extend([lib for lib in static_native_libs if lib.endswith(".cmxa")])
    cma.extend([lib for lib in static_native_libs if lib.endswith(".cma")])

    dyn_byte_libs = plugin(libname, pbyte)
    cmxa.extend([lib for lib in dyn_byte_libs if lib.endswith(".cmxa")])
    cma.extend([lib for lib in dyn_byte_libs if lib.endswith(".cma")])

    dyn_native_libs = plugin(libname, pnative)
    cmxa.extend([lib for lib in dyn_native_libs if lib.endswith(".cmxa")])
    cma.extend([lib for lib in dyn_native_libs if lib.endswith(".cma")])

    data["static_byte_libs"] = static_byte_libs
    data["static_native_libs"] = static_native_libs
    data["dyn_byte_libs"] = dyn_byte_libs
    data["dyn_native_libs"] = dyn_native_libs

    native_c_libs: List[str] = []
    native_c_lib_paths: List[str] = []

    bytecode_c_libs: List[str] = []
    bytecode_c_lib_paths: List[str] = []

    # remove dups
    cmxa = list(set(cmxa))
    cma = list(set(cma))

    for lib in cmxa:
        full_path_cmxa = os.path.join(directory, lib)
        (path_info, link_info) = find_c_libs(full_path_cmxa)
        native_c_libs.extend(link_info)
        native_c_lib_paths.extend(path_info)
    data["native_c_libs"] = native_c_libs
    data["native_c_lib_paths"] = native_c_lib_paths

    for lib in cma:
        full_path_cma = os.path.join(directory, lib)
        (path_info, link_info) = find_c_libs(full_path_cma)
        bytecode_c_libs.extend(link_info)
        bytecode_c_lib_paths.extend(path_info)
    data["bytecode_c_libs"] = bytecode_c_libs
    data["bytecode_c_lib_paths"] = bytecode_c_lib_paths

    data["dependencies"] = requires(pkg_list, libname, p0)

    jsoo_info = jsoo_runtime(libname)
    if len(jsoo_info) > 0:
        data["jsoo_runtime"] = jsoo_info

    warn = warning(libname, p0)
    if warn != "":
        data["warning"] = warn
    err = error(libname, p0)
    if err != "":
        data["error"] = err
    return data


# end of the first part.
###############################################################################

###############################################################################
# start of the second part, the contents of the file `./rules.py`.


# All path in here are "local" to the opam root directory.
# We currently prepend them with 'opam' that can be a directory on disk,
# or a symlink. This is fully configurable
class Rules:
    output_file: str
    prefix: str
    prelude: str

    def add_prefix(self, name: Union[str, None] = None) -> Union[str, None]:
        if name is None:
            return None
        return os.path.join(self.prefix, name)

    def map_prefix(self, lst: List[str]) -> List[str]:
        return [os.path.join(self.prefix, name) for name in lst]

    def __init__(self, output_file: str, prefix: str) -> None:
        self.output_file = output_file
        self.prefix = prefix
        self.prelude = "# buildifier: disable=no-effect"

        # Reset the file to an empty file
        with open(self.output_file, "w", encoding="utf-8"):
            pass

    def _open(self, fp: TextIOWrapper, indent: int, name: str) -> None:
        s = " " * indent
        fp.write("{}{}\n".format(s, self.prelude))
        fp.write("{}{}(\n".format(s, name))

    def _close(self, fp: TextIOWrapper, indent: int) -> None:
        s = " " * indent
        fp.write("{}) if not host_info().os.is_windows else None\n\n".format(s))

    def _writeln(self, fp: TextIOWrapper, indent: int, line: str) -> None:
        s = " " * indent
        fp.write("{}{}\n".format(s, line))

    def _writeln_list(
        self, fp: TextIOWrapper, indent: int, name: str, lst: List[str]
    ) -> None:
        self._writeln(fp, indent, "{} = [".format(name))
        for item in lst:
            self._writeln(fp, indent + 4, '"{}",'.format(item))
        self._writeln(fp, indent, "],")

    def command_alias(self, name: str, exe: str, resources: List[str]) -> None:
        # TODO(shaynefletcher)
        # Find a way to compute default_target_platform
        visibility = ["PUBLIC"]
        with open(self.output_file, "a", encoding="utf-8") as fp:
            self._open(fp, 0, "command_alias")
            self._writeln(fp, 4, 'name = "{}",'.format(name))
            self._writeln(fp, 4, 'exe = "{}",'.format(exe))
            self._writeln_list(fp, 4, "resources", resources)
            self._writeln_list(fp, 4, "visibility", visibility)
            self._close(fp, 0)

    def sh_binary(self, name: str, main_str: str) -> None:
        # TODO(shaynefletcher)
        # Find a way to compute default_host_platform and default_target_platform
        visibility = ["PUBLIC"]
        # As `main_str` is not `None`, `add_prefix` cannot return `None`.
        main_str = cast(str, self.add_prefix(main_str))
        with open(self.output_file, "a", encoding="utf-8") as fp:
            self._open(fp, 0, "sh_binary")
            self._writeln(fp, 4, 'name = "{}",'.format(name))
            self._writeln(fp, 4, 'main = "{}",'.format(main_str))
            self._writeln_list(fp, 4, "visibility", visibility)
            self._close(fp, 0)

    def prebuilt_cxx_library(
        self, name: str, header_dirs: List[str], header_only: bool
    ) -> None:
        # TODO: we do not add prefix for now because we pass it only
        # symlink/special names
        visibility = ["PUBLIC"]
        with open(self.output_file, "a", encoding="utf-8") as fp:
            self._open(fp, 0, "prebuilt_cxx_library")
            self._writeln(fp, 4, 'name = "{}",'.format(name))
            self._writeln_list(fp, 4, "header_dirs", header_dirs)
            self._writeln(
                fp, 4, "header_only = {},".format("True" if header_only else "False")
            )
            self._writeln_list(fp, 4, "visibility", visibility)
            self._close(fp, 0)

    def export_file(self, name: str, src: str) -> None:
        # As `src` is not `None`, `add_prefix` cannot return `None`.
        src = cast(str, self.add_prefix(src))
        with open(self.output_file, "a", encoding="utf-8") as fp:
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
        include_dir: Union[str, None] = None,
        native_lib: Union[str, None] = None,
        bytecode_lib: Union[str, None] = None,
        native_c_libs: List[str] = [],
        bytecode_c_libs: List[str] = [],
        deps: List[str] = [],
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

        with open(self.output_file, "a", encoding="utf-8") as fp:
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
def get_lib_local_path(path: str, switch_name: str) -> str:
    local_path: List[Any] = []
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
def check_file(build: str, target_dir: str, filename: str) -> Union[str, None]:
    path: str = os.path.normpath(os.path.join(target_dir, filename))
    realpath: str = os.path.normpath(os.path.join(build, path))
    if not os.path.exists(realpath):
        logging.warning("Could not find file: " + realpath)
        return None
    else:
        return path


def check_archives(build: str, target_dir: str, archives: str) -> List[str]:
    return [
        os.path.join(target_dir, a)
        for a in archives
        if check_file(build, target_dir, a)
    ]


# Process a single json entry. Most if it is automatic. However
# we don't have a clear source of truth for the dependencies to non ocaml
# libraries, like system threads or pcre, ...
# This part requires a bit of ad-hoc support still
def _process(rules: Rules, opam_switch: str, pkg: Dict[str, Any]) -> None:
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
    # In <= 4.14.0, the content of the threads/posix library is not correctly
    # installed: all the cm* files are in ocaml/threads/ but the lib*.a files
    # are in ocaml/ directly, so we need to patch that manually.
    #
    # TODO: revisit in 5.0 in case it's better
    ocaml = os.path.join(relativelib, "ocaml")

    native_c_libs: List[str] = []
    for lib in pkg["native_c_libs"]:
        patched_dir = target_dir
        if name.startswith("threads."):  # . is important. Matches .posix and .vm
            patched_dir = ocaml
        lib_name = "lib{}.a".format(lib)
        lib_path = check_file(opam_switch, patched_dir, lib_name)
        if lib_path:
            native_c_libs.append(lib_path)

    bytecode_c_libs: List[str] = []
    for lib in pkg["bytecode_c_libs"]:
        patched_dir = target_dir
        if name.startswith("threads."):  # . is important. Matches .posix and .vm
            patched_dir = ocaml
        lib_name = "lib{}.a".format(lib)
        lib_path = check_file(opam_switch, patched_dir, lib_name)
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

    # If the pkg was a ppx_deriver or rewriter, it might have some
    # ppx_runtime_deps. We'll create a secondary target, empty but for
    # these dependencies
    if "ppx_runtime_deps" in pkg:
        ppx_runtime_deps = pkg["ppx_runtime_deps"]
        rules.prebuilt_ocaml_library(
            name="{}-runtime-deps".format(name),
            include_dir=target_dir,
            bytecode_lib=None,
            native_lib=None,
            deps=ppx_runtime_deps,
        )

    # TODO(vsiles): until we have this alternative for PPXs, we still need
    # the good old cmxs export
    dyn_native_libs = pkg["dyn_native_libs"]
    for dyn_lib in dyn_native_libs:
        if dyn_lib.endswith(".cmxs"):
            short_dyn_lib = dyn_lib[:-5]
            rules.export_file(
                name="{}.{}-plugin".format(name, short_dyn_lib),
                src=os.path.join(target_dir, dyn_lib),
            )

    # Now, let's export the js_of_ocaml runtime information
    if "jsoo_runtime" in pkg:
        jsoo_runtime = pkg["jsoo_runtime"]
        for entry in jsoo_runtime:
            rules.export_file(
                name="{}.{}".format(name, entry),
                src=os.path.join(target_dir, entry),
            )


# Process the result of meta2json and export targets for buck.
# Most of it should be automated, by processing the json
# file. However we still add a few dependencies by hand, for anything
# depending on system libraries, like pcre, libev, ...
def process_json_path(rules: Rules, json_path: str, opam_switch: str) -> None:
    with open(json_path, encoding="utf-8") as fp:
        json_data = json.load(fp)

    process_json(rules, json_data, opam_switch)


# Process the result of of the first part and export targets for buck.
# Most of it should be automated, by processing the json
# file. However we still add a few dependencies by hand, for anything
# depending on system libraries, like pcre, libev, ...
def process_json(rules: Rules, json_data: Dict[str, Any], opam_switch: str) -> None:
    for pkgname in json_data:
        pkg: Dict[str, Any] = json_data[pkgname]
        _process(rules, opam_switch, pkg)


def gen_targets(
    rules: Rules,
    json_path: Union[str, None],
    local_root: str,
    opam_switch: str,
    json_data: Dict[str, Any],
) -> None:
    relativelib = "lib"
    relativebin = "bin"

    ocaml = os.path.join(relativelib, "ocaml")

    rules.prebuilt_cxx_library(
        name="ocaml-dev",
        # TODO: this is a symlink name expected by the buck2/ocaml setup
        #       reassess this in the future. We might just want to use
        #       the `ocaml` value defined above
        header_dirs=["standard_library"],
        header_only=True,
    )

    rules.export_file(name="libasmrun.a", src=os.path.join(ocaml, "libasmrun.a"))

    logging.info("Emitting 'interop_includes'")
    rules.export_file(name="interop_includes", src=os.path.join(relativelib, "ocaml"))

    # Support calling ocamldebug like e.g.
    #  `buck2 run fbcode//third-party-buck/platform010/build/supercaml:ocamldebug-exe`
    # by using a command_alias for it.
    rules.export_file(name="ocamlrun", src=os.path.join(relativebin, "ocamlrun"))

    rules.export_file(name="ocamldebug", src=os.path.join(relativebin, "ocamldebug"))

    rules.command_alias(
        name="ocamldebug-exe",
        exe=":ocamldebug",
        resources=[
            ":ocamlrun",
            ":ocamldebug",
            os.path.join(local_root, ocaml),
        ],
    )

    # TODO: fix
    logging.info("Emitting binaries")
    bin_path = os.path.join(opam_switch, relativebin)
    for b in os.listdir(bin_path):
        b_path = os.path.join(bin_path, b)

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
                rules.sh_binary(
                    name="%s-exe" % b, main_str=os.path.join(relativebin, b)
                )
            else:
                rules.sh_binary(
                    name="%s-exe" % name, main_str=os.path.join(relativebin, b)
                )

            if name == "js_of_ocaml":
                rules.export_file(
                    name="%s-runtime.js" % name,
                    src=os.path.join(relativelib, "js_of_ocaml-compiler", "runtime.js"),
                )

        if b.endswith(".byte"):
            rules.export_file(name=b, src=os.path.join(relativebin, b))

    if json_path is not None:
        logging.info("Emitting library rules based on {}".format(json_path))
        process_json_path(rules, json_path, opam_switch)
    else:
        logging.info("Emitting library rules")
        process_json(rules, json_data, opam_switch)

    logging.info("Finished generating TARGETS")


def check_argument(
    parser: argparse.ArgumentParser, name: str, param: Union[str, None]
) -> None:
    if param is None:
        print("Missing argument '{}'".format(name))
        parser.print_help()
        sys.exit(1)


# end of the second part.
###############################################################################


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dromedary.py",
        description="Read package information in the current Opam switch into buck files",
    )
    parser.add_argument("-p", "--package", help="TODO")
    parser.add_argument(
        "-j",
        "--json-output",
        help="Path to the generated JSON file containing intermediate package data. Optional.",
    )
    parser.add_argument("-i", "--input-json", help="Input json file. Optional.")
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
    data: Dict[str, Any] = {}

    args = parser.parse_args()
    json_input: Union[str, None] = args.input_json
    output_file: str = args.output
    opam_switch: Union[str, None] = args.switch

    check_argument(parser, "-o", output_file)
    check_argument(parser, "-s", opam_switch)

    # first part - script `./meta2json.py`
    if json_input is None:
        package: Union[str, None] = args.package

        pkg_list = package_list()

        if package is None:
            # no package, processing them all
            for libname in pkg_list:
                if libname in data:
                    print(
                        "Duplicated package info: {}".format(libname), file=sys.stderr
                    )
                    sys.exit(1)
                data[libname] = process_lib(pkg_list, libname)
        else:
            # package name is provided, only process this one
            data = process_lib(pkg_list, package)

        json_out_file: Union[str, None] = args.json_output
        if json_out_file is not None:
            # We truncate the file before writing
            with open(json_out_file, "wt", encoding="utf-8") as out:
                out.write(json.dumps(data, indent=4))
            sys.exit(0)
    else:
        print(f"Skipping parsing current Opam switch, --json-input is {json_input}")

    # second part - script `./rules.py`
    local_root = "opam"
    if args.root is not None:
        logging.info("overriding default 'opam' root with {}".format(args.root))
        local_root = args.root

    rules = Rules(output_file, local_root)
    gen_targets(
        rules,
        json_input,
        local_root,
        cast(str, opam_switch),
        data,
    )


if __name__ == "__main__":
    main()
