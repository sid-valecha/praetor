# Reproducible demo recording for docs/demo.gif.
#
# Render from an activated Praetor conda environment:
#
#   docs/demo_setup.sh
#   PATH="/tmp/praetor-demo-shim:$PATH" vhs docs/demo.tape
#
# The setup script creates a temporary `claude` shim so the recording exercises
# Praetor's real ClaudeCodeAdapter subprocess path without depending on Claude
# quota or network availability while recording.

Output docs/demo.gif

Require git
Require praetor

Set Shell "bash"
Set Theme "Catppuccin Mocha"
Set FontSize 24
Set Width 1440
Set Height 900
Set Padding 20
Set Framerate 24
Set PlaybackSpeed 1.0
Set TypingSpeed 18ms

Type "cd /tmp/praetor-demo"
Enter
Sleep 500ms

Type "praetor init"
Enter
Sleep 1s

Type 'git add .gitignore && git commit -m "Initialize Praetor"'
Enter
Sleep 1s

Type "praetor add --title add-greet-helper --verify ./check_greet.py"
Enter
Sleep 500ms

Type "praetor add --title add-square-helper --verify ./check_square.py"
Enter
Sleep 500ms

Type "praetor add --title add-reverse-helper --verify ./check_reverse.py"
Enter
Sleep 500ms

Type "praetor status"
Enter
Sleep 2s

Type "praetor run --max-parallel 3 --merge-strategy auto"
Enter
Sleep 3s

Type "praetor status"
Enter
Sleep 2s

Type "git log --oneline --decorate -5"
Enter
Sleep 5s
