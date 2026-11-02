' _run_hidden.vbs - run a program with no window (silent background launch)
' Usage: wscript _run_hidden.vbs "program" "arg1" "arg2" ...
' Keep this file ASCII-only: VBScript is read as ANSI, non-ASCII comments can garble.
Set sh = CreateObject("WScript.Shell")
If WScript.Arguments.Count < 1 Then WScript.Quit 1
cmd = """" & WScript.Arguments(0) & """"
For i = 1 To WScript.Arguments.Count - 1
  cmd = cmd & " """ & WScript.Arguments(i) & """"
Next
sh.Run cmd, 0, False
