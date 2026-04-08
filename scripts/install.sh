#!/usr/bin/env bash

set -euo pipefail

PACKAGE_SPEC="${HERMITCRAB_PACKAGE_SPEC:-hermitcrab-ai}"
INSTALL_ROOT="${HERMITCRAB_INSTALL_DIR:-$HOME/.local/share/hermitcrab}"
BIN_DIR="${HERMITCRAB_BIN_DIR:-$HOME/.local/bin}"
SERVICE_NAME="${HERMITCRAB_SERVICE_NAME:-hermitcrab-gateway}"
RUN_ONBOARD=true
INSTALL_SYSTEMD_USER=false
ENABLE_SYSTEMD_USER=false
START_SYSTEMD_USER=false

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[0;33m'
CYAN=$'\033[0;36m'
BOLD=$'\033[1m'
NC=$'\033[0m'

log_info() {
    printf "%s→%s %s\n" "$CYAN" "$NC" "$1"
}

log_success() {
    printf "%s✓%s %s\n" "$GREEN" "$NC" "$1"
}

log_warn() {
    printf "%s!%s %s\n" "$YELLOW" "$NC" "$1"
}

log_error() {
    printf "%s✗%s %s\n" "$RED" "$NC" "$1" >&2
}

print_banner() {
    printf "\n%s%sHermitCrab installer%s\n\n" "$CYAN" "$BOLD" "$NC"
}

append_path_line_if_missing() {
    local shell_config="$1"
    local path_line='export PATH="$HOME/.local/bin:$PATH"'

    if [[ ! -f "$shell_config" ]]; then
        return 0
    fi

    if grep -v '^[[:space:]]*#' "$shell_config" 2>/dev/null | grep -qE 'PATH=.*\.local/bin'; then
        return 0
    fi

    {
        printf "\n# HermitCrab installer\n"
        printf '%s\n' "$path_line"
    } >>"$shell_config"
    log_success "Added ~/.local/bin to PATH in $shell_config"
}

usage() {
    cat <<EOF
HermitCrab installer

Usage:
  install.sh [options]

Options:
  --skip-onboard          Do not run 'hermitcrab onboard' after install
  --systemd-user          Install a user-level systemd service for 'hermitcrab gateway'
  --enable-service        Enable the user service after installing it
  --start-service         Start the user service after installing it
  --install-dir PATH      Installation root (default: ~/.local/share/hermitcrab)
  --bin-dir PATH          Where to place the hermitcrab launcher (default: ~/.local/bin)
  --package SPEC          Package spec to install (default: hermitcrab-ai)
  -h, --help              Show this help

Examples:
  curl -fsSL https://raw.githubusercontent.com/talvasconcelos/hermitcrab/main/scripts/install.sh | bash
  curl -fsSL ... | bash -s -- --systemd-user --enable-service --start-service
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-onboard)
            RUN_ONBOARD=false
            shift
            ;;
        --systemd-user)
            INSTALL_SYSTEMD_USER=true
            shift
            ;;
        --enable-service)
            INSTALL_SYSTEMD_USER=true
            ENABLE_SYSTEMD_USER=true
            shift
            ;;
        --start-service)
            INSTALL_SYSTEMD_USER=true
            ENABLE_SYSTEMD_USER=true
            START_SYSTEMD_USER=true
            shift
            ;;
        --install-dir)
            INSTALL_ROOT="$2"
            shift 2
            ;;
        --bin-dir)
            BIN_DIR="$2"
            shift 2
            ;;
        --package)
            PACKAGE_SPEC="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

detect_python() {
    local candidate
    for candidate in python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
                PYTHON_CMD="$candidate"
                return 0
            fi
        fi
    done

    log_error "Python 3.11+ is required."
    log_info "Install Python 3.11+ and the standard venv module for your system, then rerun this script."
    exit 1
}

ensure_venv_support() {
    if ! "$PYTHON_CMD" -m venv --help >/dev/null 2>&1; then
        log_error "Your Python installation does not include venv support."
        log_info "Install your platform's Python venv package or module, then rerun this script."
        exit 1
    fi
}

create_venv() {
    mkdir -p "$INSTALL_ROOT" "$BIN_DIR"
    if [[ ! -x "$INSTALL_ROOT/venv/bin/python" ]]; then
        log_info "Creating virtual environment in $INSTALL_ROOT/venv"
        "$PYTHON_CMD" -m venv "$INSTALL_ROOT/venv"
    else
        log_info "Reusing existing virtual environment in $INSTALL_ROOT/venv"
    fi
}

install_package() {
    local pip_cmd="$INSTALL_ROOT/venv/bin/pip"
    log_info "Upgrading pip in the HermitCrab environment"
    "$pip_cmd" install --upgrade pip
    log_info "Installing $PACKAGE_SPEC"
    "$pip_cmd" install --upgrade "$PACKAGE_SPEC"

    if [[ ! -x "$INSTALL_ROOT/venv/bin/hermitcrab" ]]; then
        log_error "Install completed but hermitcrab entry point was not created."
        exit 1
    fi
}

install_launcher() {
    local launcher_path="$BIN_DIR/hermitcrab"
    local cli_path="$INSTALL_ROOT/venv/bin/hermitcrab"

    cat >"$launcher_path" <<EOF
#!/usr/bin/env bash
exec "$cli_path" "\$@"
EOF
    chmod +x "$launcher_path"
    log_success "Installed launcher at $launcher_path"
}

setup_path() {
    local login_shell original_path shell_name
    local shell_configs=()

    original_path="$PATH"
    export PATH="$BIN_DIR:$PATH"

    case ":$original_path:" in
        *":$BIN_DIR:"*)
            if [[ "$BIN_DIR" != "$HOME/.local/bin" ]]; then
                return 0
            fi
            ;;
        *)
            ;;
    esac

    if [[ "$BIN_DIR" != "$HOME/.local/bin" ]]; then
        log_warn "$BIN_DIR is not on PATH. Add it to your shell profile manually if needed."
        return 0
    fi

    login_shell="$(basename "${SHELL:-/bin/bash}")"
    case "$login_shell" in
        zsh)
            [[ -f "$HOME/.zshrc" ]] && shell_configs+=("$HOME/.zshrc")
            [[ -f "$HOME/.zprofile" ]] && shell_configs+=("$HOME/.zprofile")
            if [[ ${#shell_configs[@]} -eq 0 ]]; then
                : >"$HOME/.zshrc"
                shell_configs+=("$HOME/.zshrc")
            fi
            ;;
        bash)
            [[ -f "$HOME/.bashrc" ]] && shell_configs+=("$HOME/.bashrc")
            [[ -f "$HOME/.bash_profile" ]] && shell_configs+=("$HOME/.bash_profile")
            if [[ ${#shell_configs[@]} -eq 0 ]]; then
                : >"$HOME/.bashrc"
                shell_configs+=("$HOME/.bashrc")
            fi
            ;;
        *)
            [[ -f "$HOME/.profile" ]] && shell_configs+=("$HOME/.profile")
            ;;
    esac

    if [[ -f "$HOME/.profile" ]]; then
        case " ${shell_configs[*]} " in
            *" $HOME/.profile "*) ;;
            *) shell_configs+=("$HOME/.profile") ;;
        esac
    fi

    for shell_name in "${shell_configs[@]}"; do
        append_path_line_if_missing "$shell_name"
    done
}

run_onboard() {
    if [[ "$RUN_ONBOARD" != true ]]; then
        return 0
    fi

    log_info "Bootstrapping config and workspace"
    "$BIN_DIR/hermitcrab" onboard
    log_success "HermitCrab workspace initialized under ~/.hermitcrab"
}

install_systemd_user_service() {
    if [[ "$INSTALL_SYSTEMD_USER" != true ]]; then
        return 0
    fi

    if [[ "$(uname -s)" != "Linux" ]]; then
        log_warn "Skipping systemd service setup: this option is Linux-only."
        return 0
    fi

    if ! command -v systemctl >/dev/null 2>&1; then
        log_warn "Skipping systemd service setup: systemctl not found."
        return 0
    fi

    local systemd_user_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    local service_path="$systemd_user_dir/$SERVICE_NAME.service"
    mkdir -p "$systemd_user_dir"

    cat >"$service_path" <<EOF
[Unit]
Description=HermitCrab gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$BIN_DIR/hermitcrab gateway --log-level INFO
Restart=on-failure
RestartSec=5
WorkingDirectory=$HOME

[Install]
WantedBy=default.target
EOF

    log_success "Installed user service at $service_path"

    if ! systemctl --user daemon-reload >/dev/null 2>&1; then
        log_warn "Could not talk to systemd --user right now."
        log_info "Log in with a normal user session, then run: systemctl --user daemon-reload"
        return 0
    fi

    if [[ "$ENABLE_SYSTEMD_USER" == true ]]; then
        systemctl --user enable "$SERVICE_NAME.service"
        log_success "Enabled $SERVICE_NAME.service"
    fi

    if [[ "$START_SYSTEMD_USER" == true ]]; then
        systemctl --user start "$SERVICE_NAME.service"
        log_success "Started $SERVICE_NAME.service"
    fi

    log_info "Check status with: systemctl --user status $SERVICE_NAME.service"
    log_info "For boot-time start on headless systems, consider: sudo loginctl enable-linger $USER"
}

print_next_steps() {
    printf "\n%sInstall complete.%s\n" "$BOLD" "$NC"
    printf "Launcher: %s/hermitcrab\n" "$BIN_DIR"

    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *)
            log_warn "$BIN_DIR is not on your PATH yet."
            log_info "Add this to your shell profile:"
            printf "  export PATH=\"%s:\$PATH\"\n" "$BIN_DIR"
            ;;
    esac

    printf "\nSuggested next steps:\n"
    printf "  %s/hermitcrab doctor\n" "$BIN_DIR"
    printf "  %s/hermitcrab agent\n" "$BIN_DIR"
}

main() {
    print_banner
    detect_python
    ensure_venv_support
    create_venv
    install_package
    install_launcher
    setup_path
    run_onboard
    install_systemd_user_service
    print_next_steps
}

main "$@"
