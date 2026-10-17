#!/bin/sh
# Install the `acr` CLI for Agent Composition Review from the public GitHub repository.
#
# Usage:
#   ./install.sh
#   ACR_VERSION=v0.1.0 ./install.sh
#
# The installer tries, in order:
#   1. uv tool install
#   2. pipx install
#   3. python3 -m pip install --user
#
# It never uses sudo and never writes to the system Python site-packages.

set -eu

REPO_URL="https://github.com/meiluosi/agent-composition-review.git"
ACR_VERSION="${ACR_VERSION:-v0.1.0}"
INSTALL_SPEC="git+${REPO_URL}@${ACR_VERSION}"
PYTHON="${PYTHON:-python3}"

say() {
    printf '%s\n' "$*"
}

warn() {
    printf 'warning: %s\n' "$*" >&2
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

print_fallback() {
    cat >&2 <<EOF

Could not install the acr CLI automatically.

Install it manually, without sudo, using one of:

  uv tool install "${INSTALL_SPEC}"
  pipx install "${INSTALL_SPEC}"
  python3 -m pip install --user "${INSTALL_SPEC}"

Or isolate it in a virtual environment:

  python3 -m venv "\$HOME/.acr-venv"
  "\$HOME/.acr-venv/bin/python" -m pip install "${INSTALL_SPEC}"
  "\$HOME/.acr-venv/bin/acr" --version

No sudo is required in any of these commands.
EOF
}

if [ -z "${HOME:-}" ]; then
    die "HOME is not set; cannot determine where user-level tools are installed."
fi

# uv and pipx normally expose tools in ~/.local/bin. Put that directory first so
# the installer can verify the command it just installed.
PATH="${HOME}/.local/bin:${PATH}"
export PATH

say "Agent Composition Review installer"
say "Repository: ${REPO_URL}"
say "Version:    ${ACR_VERSION}"
say ""

INSTALLED_WITH=""

if command -v uv >/dev/null 2>&1; then
    say "==> Found uv, running 'uv tool install'"
    if uv tool install --force "${INSTALL_SPEC}"; then
        INSTALLED_WITH="uv"
    else
        warn "uv install failed; falling back to pipx."
    fi
fi

if [ -z "${INSTALLED_WITH}" ] && command -v pipx >/dev/null 2>&1; then
    say "==> Found pipx, running 'pipx install'"
    if pipx install --force "${INSTALL_SPEC}"; then
        INSTALLED_WITH="pipx"
    else
        warn "pipx install failed; falling back to 'python3 -m pip install --user'."
    fi
fi

if [ -z "${INSTALLED_WITH}" ] && command -v "${PYTHON}" >/dev/null 2>&1; then
    if "${PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
        >/dev/null 2>&1
    then
        say "==> Falling back to '${PYTHON} -m pip install --user'"
        if "${PYTHON}" -m pip install --user --upgrade "${INSTALL_SPEC}"; then
            INSTALLED_WITH="pip"
        else
            warn "'${PYTHON} -m pip install --user' failed; the environment may be externally managed."
        fi
    else
        warn "${PYTHON} is older than 3.10; this package requires Python 3.10 or newer."
    fi
fi

if [ -z "${INSTALLED_WITH}" ]; then
    print_fallback
    exit 1
fi

# Resolve the executable explicitly: ~/.local/bin may not have been on PATH when
# the shell started, and pip --user may install into ~/Library/Python/*/bin on macOS.
ACR_PATH=""
if command -v acr >/dev/null 2>&1; then
    ACR_PATH="$(command -v acr)"
else
    for candidate in \
        "${HOME}/.local/bin/acr" \
        "${HOME}/Library/Python"/*/bin/acr
    do
        if [ -x "${candidate}" ]; then
            ACR_PATH="${candidate}"
            break
        fi
    done
fi

if [ -n "${ACR_PATH}" ]; then
    say ""
    say "==> Verifying: ${ACR_PATH} --version"
    if "${ACR_PATH}" --version; then
        say ""
        say "Installed with ${INSTALLED_WITH}."
        say "Next: acr --help"
        exit 0
    fi
    warn "${ACR_PATH} was installed but '${ACR_PATH} --version' failed."
    print_fallback
    exit 1
fi

warn "Installation finished with ${INSTALLED_WITH}, but the 'acr' executable is not on PATH."
cat >&2 <<EOF

Add the tool directory to PATH, then verify:

  export PATH="\$HOME/.local/bin:\$PATH"
  acr --version

If acr was installed by pip --user into a different directory, find it with:

  "${PYTHON}" -m site --user-base
EOF
exit 1
