#!/usr/bin/env python3

# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import argparse
import json
import os
import re
import sys
from pathlib import Path
from subprocess import PIPE, Popen

# Some detailled info and experiments are described in
# T145908290 and
# https://fb.quip.com/MFTCA8gp0USa


# takes a list of strings, strips the leading/trailing spaces
# and remove the empty entries.
# We pass in an optional lambda to apply to non-empty entries
def clean_up(lst, f):
    for i in range(len(lst)):
        s = lst[i].strip()
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
def find_c_libs(full_path):
    cmd = ["ocamlobjinfo", full_path]
    process = Popen(cmd, stdout=PIPE)
    (output, err) = process.communicate()
    exit_code = process.wait()
    link_info = []
    path_info = []

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


def ocamlfind(cmd):
    query = ["ocamlfind"] + cmd
    process = Popen(query, stdout=PIPE)
    (output, err) = process.communicate()
    exit_code = process.wait()
    if exit_code != 0:
        print("Invalid cmd: {}".format(str(cmd)), file=sys.stderr)
        return ""
    return output.decode("UTF-8").strip()


def ocamlfind_query(query):
    cmd = ["query"] + query
    return ocamlfind(cmd)


def ocamlfind_list():
    cmd = ["list"]
    return ocamlfind(cmd)


def package_name(libname):
    query = ["-format", "%p", libname]
    return ocamlfind_query(query)


def package_directory(libname):
    query = ["-format", "%d", libname]
    return ocamlfind_query(query)


# these paths are comma and whitespace separated
def process_paths(s):
    lst = re.split(r"\s|,", s)
    lst2 = clean_up(lst, None)
    return lst2


# js runtime binaries
def jsoo_runtime(libname):
    query = ["-format", "%(jsoo_runtime)", libname]
    s = ocamlfind_query(query).strip().split()
    return s


# predicates are comma-separated
def archive(libname, predicates):
    if predicates == "":
        query = []
    else:
        query = ["-predicates", predicates]
    query = query + ["-format", "%a", libname]
    s = ocamlfind_query(query)
    lst = process_paths(s)
    return lst


def variable(libname, variable, predicates, recursive=False):
    query = ["-predicates", predicates, "-format", "%({})".format(variable), libname]
    if recursive:
        query = ["-recursive"] + query
    s = ocamlfind_query(query)
    lst = process_paths(s)
    return lst


def plugin(libname, predicates):
    return variable(libname, "plugin", predicates)


# opam packages can have optional dependencies, so sometimes a META
# file will reference a package that is not installed.
# For example, camlimages.all requires camlimages.graphics, which is
# not described.
#
# This function takes the list of all installed packages and use it
# to filter the results of dependencies to only existing ones.
def sanitize(pkg_list, lst):
    return list(filter(lambda s: s in pkg_list, lst))


def requires(pkg_list, libname, predicates):
    lst = variable(libname, "requires", predicates)
    return sanitize(pkg_list, lst)


def ppx_runtime_deps(pkg_list, libname, predicates):
    # vsiles:
    # We need recursivity here because some ppx don't display any
    # runtime deps but their requirements do
    lst = variable(libname, "ppx_runtime_deps", predicates, recursive=True)
    return sanitize(pkg_list, lst)


# TODO(vsiles)
# I didn't find a way to output comments in the TARGETS file.
# We might want to add warning/error entries to ocaml_external_library
# Just to output this content somewhere (and discard it during the build)
def warning(libname, predicates):
    query = ["-format", "%(warning)", libname]
    if predicates != "":
        query = ["-predicates", predicates] + query
    s = ocamlfind_query(query)
    return s


def error(libname, predicates):
    query = ["-format", "%(error)", libname]
    if predicates != "":
        query = ["-predicates", predicates] + query
    s = ocamlfind_query(query)
    return s


PLAIN = "PLAIN"
REWRITER = "REWRITER"
DERIVER = "DERIVER"


def library_kind(libname):
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


def package_list(exclude=None):
    # the package list is of the form
    # pkg1 (version ...)
    # ...
    # pkgN (version ...)
    # so we split on ')' first, and then split on whitespace to keep
    # the first part (pkg_i)
    #
    # `exclude` is a list of packages to not include in the list of packages.
    lst = ocamlfind_list().split(")")
    lst2 = clean_up(lst, lambda s: s.split()[0])
    if exclude is not None:
        print("Excluding packages {}".format(exclude))
        lst2 = list(filter(lambda p: p not in exclude, lst2))
    return lst2


def add_predicate(p, newp):
    if p == "":
        return newp
    else:
        return "{},{}".format(p, newp)


# Here we assume we want the posix threads support, so we
# do our requests with mt,mt_posix.
# If that would ever change, we need to use mt,mt_vm
def process_lib(pkg_list, libname):
    data = {}
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
    cmxa = []
    cma = []

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

    native_c_libs = []
    native_c_lib_paths = []

    bytecode_c_libs = []
    bytecode_c_lib_paths = []

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--package")
    parser.add_argument("-o", "--output")
    parser.add_argument("-e", "--exclude", nargs="*")

    args = parser.parse_args()

    package = args.package
    exclude = args.exclude

    data = None
    pkg_list = package_list(exclude)

    if package is None:
        # no package, processing them all
        data = {}
        for libname in pkg_list:
            if libname in data:
                print("Duplicated package info: {}".format(libname), file=sys.stderr)
                sys.exit(1)
            data[libname] = process_lib(pkg_list, libname)
    else:
        # package name is provided, only process this one
        data = process_lib(pkg_list, package)

    output_file = args.output
    if output_file:
        output_file = Path(args.output)
        # We truncate the file before writing
        with open(output_file, "wt") as out:
            out.write(json.dumps(data, indent=4))
    else:
        # output on stdout
        print(json.dumps(data, indent=4))


# Some helper stuff:
# jq '.[] | select(has("warning"))' dump.json
# jq '.[] | select(has("error"))' dump.json
if __name__ == "__main__":
    main()
