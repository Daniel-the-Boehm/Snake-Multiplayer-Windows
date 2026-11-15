# Snake for Windows

Snake in Python with pygame, packaged as a single `Snake.exe`. It plays alone or with up to four people on the same Wi-Fi, and it is compatible with the [macOS version](https://github.com/Daniel-the-Boehm/Snake-Multiplayer-Mac): a Windows PC can host a game that Mac players join, and the other way round.

The game is one file, `snake.py`. It also runs on Linux and macOS when started with Python.

## Features

- Seven game modes
- Local multiplayer for two to four players with automatic discovery on the network
- Four map sizes, four speeds and up to 144 apples on the board at once
- Four colour themes and four snake styles
- Your own pictures as background, snake head or fruit: drag an image into the window
- Settings and high scores are saved between sessions

## Game modes

| Mode | Rule |
| --- | --- |
| Classic | Solid borders. Hit a wall or yourself and the round is over. |
| No Borders | The edges wrap around. |
| Wall | Every apple turns the end of your tail into a permanent wall. |
| Twin | After each apple, head and tail swap and you move the other way. |
| Mirror | Left and right are swapped. |
| Rush | The more you eat, the faster you go. |
| Apple | You play the apple. Alone, a computer-controlled snake hunts you. With friends, they are the snakes and have 60 seconds to catch you. |

## Run from source

Requirements: Python 3.12 or 3.13.

```
py -3.13 -m pip install -r requirements.txt
py -3.13 snake.py
```

## Build Snake.exe

Double-click `build_exe.bat`. It installs pygame and PyInstaller, checks that pygame loads and writes the finished game to `dist\Snake.exe`. The file runs on any 64-bit Windows 10 or 11 PC without Python.

Windows SmartScreen warns on first start because the file is not signed: choose More info, then Run anyway. A step-by-step guide in German, including troubleshooting, is in [ANLEITUNG.md](ANLEITUNG.md).

## Controls

| Key | Action |
| --- | --- |
| Arrow keys or WASD | Start and steer |
| P | Pause |
| R | Restart, or teleport in Apple mode |
| Esc | Settings |
| M | Multiplayer |
| Space or Enter | Play again |
| F11 | Full screen |
| F3 | Show frame rate |

## Multiplayer

One player hosts, the others join. All devices have to be on the same network, and the network has to be set to Private in Windows. When you host for the first time, the Windows firewall asks whether Snake may use the network: tick Private networks and allow access.

The game uses TCP port 55055 for the match and UDP port 55056 to find hosts. If discovery fails, enter the host's IP address by hand in the join screen.

## Saved data

Everything is stored in `%APPDATA%\Snake`:

| File | Content |
| --- | --- |
| `snake_save.json` | Settings and high scores |
| `snake_photos\` | Custom pictures |
| `snake_error.log` | Crash report, if the game stops unexpectedly |
