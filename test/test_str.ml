let () =
  Printf.printf "%s\n"
    (Str.replace_first
       (Str.regexp {|hello \([A-Za-z]+\)|})
       {|\1|} "hello world")
