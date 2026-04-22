#!/bin/sh
# Install hermes-shell for the current Unix account.
#
# What this script does:
# 1. Creates a local virtualenv in the project directory.
# 2. Installs this project into that virtualenv in editable mode.
# 3. Writes a small launcher script to ~/.local/bin/hermes-shell-login.
#
# What it does NOT do:
# - it does not install Hermes itself
# - it does not touch /etc/shells
# - it does not change your login shell
# - it does not configure getty, sshd, or serial stty settings
#
# Run this as the user who should own and run hermes-shell.
# Example:
#   cd ~/play/hermes-shell
#   ./scripts/install-local.sh
#
# After running it, test with:
#   ~/.local/bin/hermes-shell-login

set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
VENV="$ROOT/.venv"
BIN_DIR="${HOME}/.local/bin"
WRAPPER="$BIN_DIR/hermes-shell-login"

printf 'Installing hermes-shell for user: %s\n' "$(id -un)"
printf 'Project root: %s\n' "$ROOT"
printf 'Virtualenv:    %s\n' "$VENV"
printf 'Launcher:      %s\n' "$WRAPPER"
printf '\n'

# Create or refresh a project-local virtualenv.
python3 -m venv "$VENV"

# Install this project into the virtualenv. Editable mode means changes in
# the checkout are picked up without reinstalling the package each time.
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -e "$ROOT"

# Write a tiny login-shell-friendly launcher in ~/.local/bin.
mkdir -p "$BIN_DIR"
cat >"$WRAPPER" <<EOF
#!/bin/sh
set -eu
# Defaults are teletype-friendly, but caller-provided values win.
export TERM="\${TERM:-tty33}"
export COLUMNS="\${COLUMNS:-72}"
export LINES="\${LINES:-24}"
exec "$VENV/bin/hermes-shell" "\$@"
EOF
chmod +x "$WRAPPER"

printf 'Install complete.\n\n'
printf 'What you now have:\n'
printf '  - project virtualenv: %s\n' "$VENV"
printf '  - launcher script:    %s\n' "$WRAPPER"
printf '\n'
printf 'Typical next steps:\n'
printf '  1. Test interactively:\n'
printf '       %s\n' "$WRAPPER"
printf '  2. If you want to use it as a real login shell, add it to /etc/shells.\n'
printf '  3. Then change the target account shell with chsh, or use sshd ForceCommand.\n'
printf '\n'
printf 'Reminder: this script does not install Hermes itself. The `hermes` command\n'
printf 'must already exist in PATH for the wrapper to work.\n'