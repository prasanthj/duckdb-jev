Output "docs/images/performance-demo.gif"

Set Shell "bash"
Set FontFamily "Menlo"
Set FontSize 20
Set Width 1280
Set Height 920
Set Padding 32
Set Framerate 30
Set PlaybackSpeed 1
Set Theme "Dracula"
Set TypingSpeed 7ms

Hide
Type "export PS1='$ '; clear"
Enter
Sleep 400ms
Show
Type "uv run python -m examples.performance_demo"
Enter
Wait+Screen /COLD RUN/
Wait+Screen /classifications complete/
Sleep 1800ms
Wait+Screen /WARM REPLAY/
Wait+Screen /Cached replay complete/
Sleep 3500ms
