#!/usr/bin/env python3

# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# This script has been tested with Python 3.8.18 and later.

"""
Script to parse an Opam sandbox and generate a `BUCK` file containing its packages.

This uses two existing scripts, `./meta2json.py` and `./rules.py`.
"""

import argparse
import json
import os
import re
import subprocess  # nosec
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

JSON_SCRIPT_NAME = "meta2json.py"
"""The name of the script to generate the JSON file from the packages in the
current Opam switch."""

RULE_SCRIPT_NAME = "rules.py"
"""The name of the script to generate the `BUCK` file from the JSON output of
`JSON_SCRIPT_NAME`."""

INTERMEDIATE_JSON = tempfile.mkstemp(suffix=".json", prefix="buck_opam")[1]
"""The path of a temporary file to write and read the intermediate JSON to and from.
MUST always be deleted, even if not used."""

EXCLUDE_PACKAGES = ["dune.configurator"]
"""The list of package names generated by `ocamlfind` which should be
excluded because they generate errors in the `BUCK` file."""

OPAM_EXE = "opam"
"""The cmd to call opam with."""

OPAM_SWITCH_ENV_CMD = f"{OPAM_EXE} env --set-switch"
"""The command to call Opam with to get the environment of a switch.
With no additional arguments this shows the environment of the current
switch."""

OPAM_SWITCH_ENV_SET_CMD = f"{OPAM_SWITCH_ENV_CMD} --switch"
"""The command to call Opam to get the environment of a named switch.
MUST de followed by the name of the switch to display the environment of."""

OPAM_SWITCH_CREATE_CMD = [OPAM_EXE, "switch", "create"]
"""The command to call Opam with to create a new Opam switch.
MUST be followed by at least the name or path of the switch."""

OPAM_INSTALL_COMMAND = [OPAM_EXE, "install"]
"""The command to call Opam with to install a list of packages.
MUST be followed by a non-empty list of packages to install."""

OPAM_PATH_NAME = "OPAM_SWITCH_PREFIX"
"""The name of the environment variable of the output of
`OPAM_SWITCH_ENV_CMD` containing the path to the current or given opam
switch."""

OPAM_SWITCH_NAME = "OPAMSWITCH"
"""The name of the environment variable of the output of
`OPAM_SWITCH_ENV_CMD` containing the name of the current or given opam
switch."""

DEFAULT_OPAM_SWITCH_NAME = "./"
"""The name (well, path) of the Opam switch to generate, if none is given in
the config file."""

DEFAULT_OPAM_COMPILER_NAME = "ocaml-variants"
"""The compiler name to pass to `opam switch` if none is given in the config
file."""

PYTHON = sys.executable
"""Path to the python executable this script is called with"""


###############################################################################
def error_exit(code: int) -> Any:
    """Exit with return code `code` and delete the temporary file
    `INTERMEDIATE_JSON`."""
    os.remove(INTERMEDIATE_JSON)
    sys.exit(code)


###############################################################################
def opam_switch_env(switch: Optional[str]) -> Dict[str, str]:
    """Return the environment variables of Opam switch with the name `switch`
    or the current one, if `switch` is `None`.

    Args:
        switch (Optional[str]): The name of the Opam switch to use. If this
        is `None`, the currently active Opam switch is used.

    Returns:
        Dict[str, str]: The environment variables `opam env` sets.
    """
    cmd = OPAM_SWITCH_ENV_CMD
    if switch is not None:
        cmd = f"{OPAM_SWITCH_ENV_SET_CMD} {switch}"
    out = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        check=False,
    )  # nosec
    if out.returncode != 0:
        print(
            "Error, the opam command '{args}' returned code '{code}':\n{err}".format(
                args=out.args, code=out.returncode, err=out.stderr.decode("utf-8")
            ),
            file=sys.stderr,
        )
        return error_exit(2)
    else:
        ret_val: Dict[str, str] = {}
        out_std = out.stdout.decode("utf-8")
        env_regex = r"^(?P<name>[A-Z_]+)\s*=\s*['\"](?P<value>.*)['\"];\s*export.*;\s*$"
        for name_match in re.finditer(env_regex, out_std, re.UNICODE | re.MULTILINE):
            ret_val[name_match.group("name")] = name_match.group("value")
        if not ret_val:
            print(
                f"Error: could not successfully parse the output of `opam env`:\n{out_std}\nUsing regex {env_regex}",
                file=sys.stderr,
            )
            return error_exit(3)
        return ret_val


###############################################################################
def opam_switch_path(opam_env: Dict[str, str]) -> Path:
    """Return the path to the Opam switch of the given environment dictionary.

    Args:
        opam_env (Dict[str, str]): The Opam environment to use.

    Returns:
        Path: The path to the Opam switch of the given environment dictionary.
    """
    return Path(opam_env[OPAM_PATH_NAME]).absolute()


###############################################################################
def run_command(cmd_args: List[Any], cmd_env: Dict[str, str]) -> None:
    """Run the given command `cmd_args` in a shell with the environment
    `cmd_env` set.

    Args:
        cmd_args (List[Any]): The command line arguments to pass to the shell.
        cmd_env (Dict[str, str]): The environment to run the command in.
    """
    out = subprocess.run(
        " ".join(map(str, cmd_args)),
        env=cmd_env,
        shell=True,
        capture_output=True,
        check=False,
    )  # nosec
    if out.returncode != 0:
        print(
            f"Error, the command {out.args} returned {out.returncode}\n{out.stderr.decode('utf-8')}",
            file=sys.stderr,
        )
        return error_exit(2)
    else:
        print_output(out)


###############################################################################
def print_output(out: subprocess.CompletedProcess) -> None:
    """Print the output of the given finished process."""
    std_out = out.stdout.decode("utf-8")
    err_out = out.stderr.decode("utf-8")
    print(std_out + err_out)


###############################################################################
def run_cmd_output(cmd_args: List[Any], cmd_env: Optional[Dict[str, str]]) -> str:
    """Run the given command in a shell.

    Prints the output of the process in "real time".

    Args:
        cmd_args (List[Any]): The command and it's arguments to run.
        cmd_env (Optional[Dict[str, str]]): The environment to run the command
                                            in.

    Returns:
        str: The accumulated stdout and stderr output of the process.
    """
    std_out: str = ""
    with subprocess.Popen(
        " ".join(map(lambda e: f"'{str(e)}'", cmd_args)),
        shell=True,
        env=cmd_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ) as p:  # nosec
        while p.poll() is None:
            std_str = p.stdout.read1().decode("utf-8")
            std_out += std_str
            print(std_str, end="", flush=True, file=sys.stdout)

        if p.poll() != 0:
            print(f"Error: command '{p.args}' returned '{p.poll()}'")
            return error_exit(6)

    return std_out


###############################################################################
def generate_switch(config_file: str) -> Tuple[str, Dict[str, Any]]:
    """Generate an Opam switch from the configuration in the given JSON file
    `config_file`.

    Args:
        config_file (str): The path to the JSON config file to use.

    Returns:
        Tuple[str, Dict[str, Any]]: The name of the generated Opam switch and
                                    the content of the JSON file `config_file`.
    """
    print(f"Using config file '{config_file}'")
    switch_config = read_json(config_file)

    gen_switch_name = switch_config.get("name")
    if gen_switch_name is None:
        gen_switch_name = DEFAULT_OPAM_SWITCH_NAME

    if gen_switch_name.startswith("."):
        base_dir = Path(config_file).absolute().parent
        gen_switch_name = base_dir.joinpath(gen_switch_name).resolve()

    gen_switch_compiler = switch_config.get("compiler")
    if gen_switch_compiler is None:
        gen_switch_compiler = DEFAULT_OPAM_COMPILER_NAME
    print(
        f"Generating Opam switch '{gen_switch_name}' using compiler '{gen_switch_compiler}'"
    )
    if switch_config.get("packages") is None:
        print(
            "Error: no packages to install found in JSON configuration. Array 'packages' is missing!"
        )
        return error_exit(8)

    switch_name_rex = r"^\s*#\s*Run\s*eval\s*\$\(opam\s*env\s*--switch=(?P<name>[^)]+)\)\s*to\s*update"
    create_args = OPAM_SWITCH_CREATE_CMD
    create_args.extend([gen_switch_name, gen_switch_compiler])
    std = run_cmd_output(create_args, cmd_env=None)
    match = re.search(switch_name_rex, std, re.UNICODE | re.DOTALL | re.MULTILINE)
    if match is None:
        print(
            f"Error: could not successfully parse the output of 'opam switch create':\n{std}\nUsing regex {switch_name_rex}",
            file=sys.stderr,
        )
        return error_exit(7)
    else:
        return match.group("name"), switch_config


###############################################################################
def read_json(path: str) -> Dict[str, Any]:
    """Return the content of the JSON file `path`.

    Args:
        path (str): The path to the JSON file to parse.

    Returns:
        Dict[str, Any]: The contents of the JSON file `path`.
    """
    try:
        with open(path, "rt", encoding="utf-8") as file:
            switch_config = json.load(file)
    except FileNotFoundError as exc:
        print(
            f"Error: configuration file '{path}' not found:\n{exc}",
            file=sys.stderr,
        )
        return error_exit(4)
    except json.decoder.JSONDecodeError as exc:
        print(f"Error: file '{path}' JSON parsing error:\n{exc}")
        return error_exit(5)
    except Exception as exc:
        print(f"Error: exception caught:\n{exc}")
        return error_exit(6)

    return switch_config


###############################################################################
def install_packages(switch_config: Dict[str, Any], cmd_env: Dict[str, str]) -> None:
    """Install the packages given in `switch_config["packages"]`.

    Uses the Opam environment `cmd_env`.

    Args:
        switch_config (Dict[str, Any]): The configuration containing the list
                                        of packages to install.
        cmd_env (Dict[str, str]): The environment to run the `opam install`
                                  command in.
    """
    gen_switch_packages: Optional[List[str]] = switch_config.get("packages")
    if gen_switch_packages is None:
        print(
            "Error: no packages to install found in JSON configuration. Array 'packages' is missing!"
        )
        return error_exit(8)
    print(f"Installing packages '{gen_switch_packages}'")
    inst_args = OPAM_INSTALL_COMMAND
    inst_args.extend(gen_switch_packages)
    _ = run_cmd_output(inst_args, cmd_env=cmd_env)


###############################################################################
def parse_command_line() -> argparse.Namespace:
    """
    Parse the command line.

    Return the parsed command line arguments.

    Returns:
        `argparse.Namespace`: The parsed command line arguments.
    """
    parser = argparse.ArgumentParser(
        prog="dromedary.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Generate a Buck 2 configuration file 'BUCK_FILE' containing all packages of the
current or a given Opam switch.

Examples:

  To add all packages of the current Opam switch to the file ./third-party/BUCK
    python3 dromedary.py -o ./third-party/BUCK

  To add all packages of the Opam switch 'switch_name' to the file
  ./third-party/BUCK:
    python3 dromedary.py -s switch_name -o ./third-party/BUCK

  To add all packages of the current Opam switch except ocaml-lsp-server to the
  file ./third-party/BUCK:
    python3 dromedary.py -e ocaml-lsp-server -o ./third-party/BUCK

  To generate a new Opam switch using the configuration file
  ./dromedary.json and generate the file ./third-party/BUCK:
    python3 dromedary.py -o ./third-party/BUCK ./dromedary.json""",
        epilog="For details, see https://github.com/facebook/ocaml-scripts.",
    )

    parser.add_argument(
        "config_file",
        metavar="CONFIG_FILE",
        nargs="?",
        help=(
            "If a JSON configuration file with Opam switch and package "
            "information is given, a new Opam switch is generated. Optional."
        ),
    )

    parser.add_argument(
        "-o",
        "--output",
        metavar="BUCK_FILE",
        required=True,
        help="Output buck file name. Mandatory.",
    )

    parser.add_argument(
        "-s",
        "--switch",
        metavar="SWITCH",
        required=False,
        help="Use Opam switch SWITCH instead of the current one. Optional.",
    )

    parser.add_argument(
        "-e",
        "--exclude",
        nargs="*",
        metavar="PACKAGE",
        required=False,
        help="A list of space separated package names to exclude from BUCK_FILE. Optional.",
    )

    parser.add_argument(
        "-r",
        "--root",
        metavar="ROOT",
        required=False,
        default="opam",
        help="Relative path used as a root for all paths in the generated file. Optional, default is 'opam'.",
    )

    parser.add_argument(
        "-j",
        "--json",
        metavar="JSON_FILE",
        required=False,
        help="The name of the intermediate JSON file to generate. Optional.",
    )

    parser.add_argument(
        "-p",
        "--package",
        metavar="PACKAGE",
        required=False,
        help="Only process this single opam package PACKAGE. Optional.",
    )

    return parser.parse_args()


###############################################################################
def main() -> None:
    """Main entry point of this script."""
    source_dir = Path(__file__).parent.resolve()
    json_script = source_dir.joinpath(JSON_SCRIPT_NAME)
    if not json_script.exists():
        print(f"ERROR: script file {json_script} not found!", file=sys.stderr)
        return error_exit(1)
    rules_script = source_dir.joinpath(RULE_SCRIPT_NAME)
    if not rules_script.exists():
        print(f"ERROR: script file {rules_script} not found!", file=sys.stderr)
        return error_exit(1)

    args = parse_command_line()

    output_file: str = args.output
    print(f"Generating buck2 file {output_file}")

    exclude_list = args.exclude

    switch_name = args.switch

    config_file: Optional[str] = args.config_file
    switch_config: Dict[str, Any] = {}
    if config_file is not None:
        switch_name, switch_config = generate_switch(config_file)
        print("")

    opam_env = opam_switch_env(switch_name)
    switch_name = opam_env[OPAM_SWITCH_NAME]
    sys_env = os.environ.copy()
    cmd_env = {**sys_env, **opam_env}
    print(f"Using Opam switch {switch_name}")
    switch_path = opam_switch_path(opam_env)
    print(f"Using Opam path: {switch_path}")

    if config_file is not None:
        install_packages(switch_config, cmd_env)
        print("")

    local_root: str = args.root
    link_dest = Path(output_file).parent.joinpath(local_root).absolute()
    print(f"Trying to link {switch_path} to {link_dest}")
    if link_dest.exists():
        os.remove(link_dest)
    os.symlink(switch_path, link_dest)

    intermediate_json = args.json
    if intermediate_json is None:
        intermediate_json = INTERMEDIATE_JSON

    print("")

    print(f"Running script {json_script}...")
    cmd_args = [PYTHON, json_script, "-o", intermediate_json]
    package: Union[str, None] = args.package
    if package is not None:
        cmd_args.append("-p")
        cmd_args.append(package)
    cmd_args.append("-e")
    cmd_args.extend(EXCLUDE_PACKAGES)
    if exclude_list is not None:
        cmd_args.extend(exclude_list)
    run_command(cmd_args, cmd_env)

    print(f"Running script {rules_script}...")
    cmd_args = [
        PYTHON,
        rules_script,
        "-o",
        output_file,
        "-s",
        switch_path.absolute(),
        "-i",
        intermediate_json,
        "-r",
        local_root,
    ]
    run_command(cmd_args, cmd_env)

    os.remove(INTERMEDIATE_JSON)  # always remove the temporary file.


if __name__ == "__main__":
    main()
