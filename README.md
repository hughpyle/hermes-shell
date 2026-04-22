# hermes-shell

A minimal line-oriented shell that wraps `hermes chat -Q` and keeps a
Hermes session alive across turns.

It is meant for hardcopy terminals and teletype-like links:

- ASCII only on output (with BEL pass-through)
- strict wrapping to terminal width, default 72 columns
- preserves leading whitespace and blank lines (safe for ASCII art)
- binary mode for tape punch via base64-delimited segments
- no Hermes TUI, no slash completion, no banner, no color
- respects `TERM`, `COLUMNS`, and `LINES`
- injects terminal constraints into Hermes via
  `HERMES_EPHEMERAL_SYSTEM_PROMPT`

This project stays separate from Hermes core. It shells out to the
installed `hermes` command instead of patching Hermes internals.

## 30-second quickstart

```sh
cd ~/play/hermes-shell
./install.sh
~/.local/bin/hermes-shell-login
```

Then type normal prompts.

Local commands:

- `.reset` or `.new` starts a fresh Hermes conversation
- `.exit` or `.quit` leaves the shell
- `Ctrl-D` leaves the shell

Everything else is sent to Hermes as plain text.

## what the wrapper actually does

This shell does not use Hermes' interactive TUI.
Instead, for each user turn it runs a command like:

```sh
hermes chat -Q -q "your prompt here"
```

and, after the first turn, adds:

```sh
--resume <session_id>
```

That means:

- conversation continuity is preserved across turns inside the wrapper
- Hermes still writes to its normal session store and uses its normal
  memory/features
- if the wrapper exits, the wrapper itself does not automatically reopen
  the last session on restart
- if you want a truly resumed old session later, use Hermes directly or
  extend the wrapper

## TERM and terminal behavior

The wrapper respects terminal settings in two ways.

First, it passes environment through to the Hermes subprocess:

- `TERM`
- `COLUMNS`
- `LINES`
- `NO_COLOR=1`

If these are not already set, the install wrapper defaults them to:

- `TERM=tty33`
- `COLUMNS=72`
- `LINES=24`

Second, it builds a system-prompt addendum from
`hermes_shell/system_prompt.txt` and passes it through
`HERMES_EPHEMERAL_SYSTEM_PROMPT`, telling Hermes that it is talking to a
hardcopy terminal with those constraints. You can edit that file directly
to change what Hermes is told about the terminal.

Important: `hermes-shell` does not itself interpret terminfo or emulate a
terminal. It is a plain text wrapper that:

- tells Hermes about the terminal constraints
- reformats Hermes output locally
- strips non-ASCII characters (preserving BEL)
- wraps long lines to the selected width
- preserves short lines, indentation, and blank lines verbatim

## binary mode

The shell supports an 8-bit binary data path, useful for punching tape
on devices like the ASR33 in binary mode.

Hermes can emit raw binary data by base64-encoding it between
`<<BINARY>>` and `<<END>>` markers, each on its own line. The shell
decodes the base64 content and writes the raw bytes directly to stdout,
bypassing ASCII sanitization and text wrapping. Normal text before and
after the markers is displayed normally.

This is always available — no flag needed. The system prompt tells
Hermes about the capability and instructs it to use binary mode only
when the user asks to punch tape or send binary data.

## local commands

The shell itself understands only:

- `.reset` or `.new` — drop the current Hermes session and start fresh
- `.exit` or `.quit` — leave the shell
- `Ctrl-D` — leave the shell

Everything else is sent to Hermes as plain text.

## install

The `install.sh` script is a convenience installer for one Unix
account.

It does exactly three things:

1. creates a Python virtualenv in `~/play/hermes-shell/.venv`
2. installs this project into that virtualenv in editable mode
3. writes a launcher script at `~/.local/bin/hermes-shell-login`

It does not:

- install Hermes itself
- add anything to `/etc/shells`
- change your login shell
- configure sshd, getty, or serial `stty`

So the intended flow is:

1. run `install.sh` as the target user
2. test `~/.local/bin/hermes-shell-login`
3. only then wire it into ssh or getty

Run it like this:

```sh
cd ~/play/hermes-shell
./install.sh
```

After it finishes, test the wrapper:

```sh
~/.local/bin/hermes-shell-login
```

That should drop you into the minimal Hermes shell.
If that works, then proceed to login-shell or getty setup.

Files created by the installer:

- project virtualenv: `~/play/hermes-shell/.venv`
- user launcher: `~/.local/bin/hermes-shell-login`

The launcher is intentionally tiny. It just sets teletype-friendly
defaults if they are not already present:

- `TERM=tty33`
- `COLUMNS=72`
- `LINES=24`

and then execs the virtualenv's `hermes-shell` command.

You can also run it directly without installing the wrapper:

```sh
cd ~/play/hermes-shell
python -m hermes_shell.shell
```

## options

```sh
hermes-shell-login --help
```

Useful flags:

- `--columns 72`     hard wrap width
- `--lines 24`       advertised screen height
- `--term tty33`     terminal type handed to Hermes
- `--hermes-bin ...` use a non-default Hermes executable
- `--max-turns 90`   max Hermes agent turns per prompt
- `--model ...`      pin a Hermes model
- `--provider ...`   pin a Hermes provider
- `--toolsets ...`   restrict Hermes toolsets
- `--skills ...`     enable specific Hermes skills

## copy-paste ssh account setup

Simplest route: create a dedicated Unix account and make the wrapper its
login shell.

1. create the user:

```sh
sudo useradd -m -s /bin/bash hermes
sudo passwd hermes
```

2. install the wrapper as that user:

```sh
sudo -u hermes -H sh -lc '
cd ~/play/hermes-shell && ./install.sh
'
```

3. add the wrapper to `/etc/shells`:

```sh
echo /home/hermes/.local/bin/hermes-shell-login | sudo tee -a /etc/shells
```

4. change the account shell:

```sh
sudo chsh -s /home/hermes/.local/bin/hermes-shell-login hermes
```

5. test it:

```sh
ssh hermes@host
```

Now `ssh hermes@host` drops straight into the Hermes shell.

If you prefer to keep `/bin/bash` as the login shell, use an sshd match
block instead. See `examples/sshd_config.fragment`.

## ssh ForceCommand alternative

If you do not want to change the account shell, add a Match block like:

```sshconfig
Match User hermes
    PermitTTY yes
    X11Forwarding no
    AllowTcpForwarding no
    ForceCommand /home/hermes/.local/bin/hermes-shell-login
```

A fragment is included at `examples/sshd_config.fragment`.

## serial getty setup on linux

The ASR33 notes in `~/play/asr33/rpi/README.md` are the model here.

For a serial device like `/dev/ttyACM0`, create a systemd override for
`getty@ttyACM0.service` like this:

```ini
[Service]
Type=simple
ExecStart=
ExecStart=-/sbin/agetty --nohostname --autologin hermes --noclear ttyACM0 110 tty33
```

An example file is included at `examples/getty-override.conf`.

With a dedicated `hermes` account whose login shell is
`/home/hermes/.local/bin/hermes-shell-login`, the boot/login flow is:

1. `agetty` owns the serial line
2. it autologins the `hermes` user
3. that user's login shell is `hermes-shell-login`
4. the wrapper starts the minimal Hermes shell

## two common linux serial cases

Case 1: true 110-baud teletype or direct serial discipline

Use a real getty/tty setup and expect to tune `stty` yourself. Typical
post-login settings may look like:

```sh
stty ispeed 110 ospeed 110 icrnl xcase iexten ofill cr1
```

This matches the guidance in `~/play/asr33/rpi/README.md`.

Case 2: USB/Teensy/Arduino adapter that already smooths out terminal
quirks

You may still use `tty33` as the TERM value, but you may not need actual
110-baud host-side tty settings or the same delays. The wrapper's width
control may be enough, with less `stty` tuning.

## error handling

Hermes subprocess failures are caught and reported as a single concise
line (`error: ...`), then the shell returns to the prompt. No tracebacks
are printed — important when output goes to a mechanical device where
long uninterruptible output is costly.

The shell handles SIGHUP (ssh disconnect) with a clean exit.

## limitations

This is intentionally small and conservative.

Current limitations:

- no full-screen TUI
- no slash commands inside the wrapper
- no command completion
- no autosuggest
- only `.reset`, `.new`, `.exit`, and `.quit` are local commands
- the wrapper depends on `hermes chat -Q` emitting a parseable
  `session_id:` line
- wrapper session continuity lasts only while the wrapper process is
  alive
- this wrapper formats plain text; it is not a terminfo emulator and it
  does not replace `stty`, `agetty`, or serial line setup

## design notes

This wrapper intentionally uses one Hermes subprocess per user turn.
That keeps it decoupled from Hermes internals and lets it follow Hermes
upgrades without patching the core TUI.

Session continuity is preserved by parsing the `session_id:` line from
`hermes chat -Q` and feeding it back with `--resume` on the next turn.

The system prompt is loaded from `hermes_shell/system_prompt.txt` at
runtime, so you can edit it without touching the Python code.
