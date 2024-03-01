#include <stdio.h>

#include <caml/version.h>

char const* ocaml_version() {
  return OCAML_VERSION_STRING;
}

int main() {
  printf("The OCaml toolchain, version %s\n", ocaml_version());

  return 0;
}
