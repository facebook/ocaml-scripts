let _ =
  try Printf.printf "Hello '%s'!\n" (Unix.getlogin ()) with
  | Unix.Unix_error (Unix.ENOENT, _, _) -> (
      try Printf.printf "Hello '%d'!\n" (Unix.getpid ()) with _ -> ())
  | _ -> ()
