Output "docs/images/terminal-demo.gif"

Set Shell "bash"
Set FontFamily "Menlo"
Set FontSize 22
Set Width 1800
Set Height 1120
Set Padding 30
Set Framerate 30
Set PlaybackSpeed 1
Set Theme "Dracula"
Set TypingSpeed 8ms

Hide
Type "export PS1='$ '; clear"
Enter
Sleep 500ms
Show
Type "uv run python examples/terminal_demo.py"
Enter
Wait+Screen /D SELECT account, jev_choice/
Sleep 1s
Wait+Screen /Cache hits:/
Sleep 3s
