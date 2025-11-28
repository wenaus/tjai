#!/usr/bin/env bash
# system_status.sh - Check and report on tjai/primus server infrastructure
# Usage: ./system_status.sh [section]
# Sections: all, python, apache, postgres, deploy, tjai, primus

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

section="${1:-all}"

ok() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }
warn() { echo -e "${YELLOW}!${NC} $1"; }
info() { echo -e "${CYAN}→${NC} $1"; }
header() { echo -e "\n${CYAN}=== $1 ===${NC}"; }

check_python() {
    header "Python Environment"
    if command -v python3 &>/dev/null; then
        ok "Python3: $(python3 --version 2>&1)"
        info "Path: $(which python3)"
    else
        fail "Python3 not found"
    fi

    if dpkg -l python3-venv &>/dev/null 2>&1; then
        ok "python3-venv installed"
    else
        fail "python3-venv not installed"
    fi

    if dpkg -l python3-pip &>/dev/null 2>&1; then
        ok "python3-pip installed"
    else
        fail "python3-pip not installed"
    fi
}

check_apache() {
    header "Apache Web Server"
    if [ -x /usr/sbin/apache2 ]; then
        ok "Apache2: $(/usr/sbin/apache2 -v 2>&1 | head -1)"
    else
        fail "Apache2 not installed"
        return
    fi

    if systemctl is-active --quiet apache2; then
        ok "Apache2 service: running"
    else
        fail "Apache2 service: not running"
    fi

    if dpkg -l libapache2-mod-wsgi-py3 &>/dev/null 2>&1; then
        ok "mod_wsgi-py3 installed"
    else
        fail "mod_wsgi-py3 not installed"
    fi

    if apache2ctl -M 2>/dev/null | grep -q wsgi_module; then
        ok "mod_wsgi loaded"
    else
        warn "mod_wsgi not loaded (may need: sudo a2enmod wsgi)"
    fi

    # Check sites
    info "Enabled sites:"
    if [ -d /etc/apache2/sites-enabled ]; then
        ls -1 /etc/apache2/sites-enabled/ 2>/dev/null | while read site; do
            echo "    $site"
        done
    fi
}

check_postgres() {
    header "PostgreSQL Database"
    if command -v psql &>/dev/null; then
        ok "PostgreSQL client: $(psql --version 2>&1)"
    else
        fail "PostgreSQL client not installed"
        return
    fi

    if systemctl is-active --quiet postgresql; then
        ok "PostgreSQL service: running"
    else
        fail "PostgreSQL service: not running"
        return
    fi

    # Check databases (requires sudo -u postgres or proper pg_hba.conf)
    info "Checking databases..."
    for db in primus tjai; do
        if sudo -u postgres psql -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw "$db"; then
            ok "Database '$db' exists"
        else
            warn "Database '$db' does not exist"
        fi
    done

    # Check users
    info "Checking database users..."
    for user in primus tjai; do
        if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$user'" 2>/dev/null | grep -q 1; then
            ok "User '$user' exists"
        else
            warn "User '$user' does not exist"
        fi
    done
}

check_deploy() {
    header "Deployment Directories"
    for dir in /var/www/primus /var/www/tjai; do
        if [ -d "$dir" ]; then
            ok "Directory exists: $dir"
            if [ -f "$dir/.env" ]; then
                ok "  .env file present"
            else
                warn "  .env file missing"
            fi
            if [ -d "$dir/.venv" ]; then
                ok "  virtualenv present"
            else
                warn "  virtualenv missing"
            fi
            if [ -f "$dir/manage.py" ]; then
                ok "  Django manage.py present"
            else
                warn "  Django manage.py missing"
            fi
        else
            warn "Directory missing: $dir"
        fi
    done
}

check_tjai() {
    header "TJAI Application"
    local dev_dir=/home/admin/github/tjrepo/tjai
    local prod_dir=/var/www/tjai

    info "Dev directory: $dev_dir"
    if [ -d "$dev_dir" ]; then
        ok "Dev directory exists"
        if [ -f "$dev_dir/tj.py" ]; then
            ok "  tj.py (CLI) present"
        fi
    else
        fail "Dev directory missing"
    fi

    info "Production directory: $prod_dir"
    if [ -d "$prod_dir" ]; then
        ok "Production directory exists"
    else
        warn "Production directory not deployed"
    fi

    # Check local tj command
    if command -v tj &>/dev/null || type tj &>/dev/null 2>&1; then
        ok "tj command available"
    else
        warn "tj command not in PATH (check ~/.bashrc)"
    fi
}

check_primus() {
    header "Primus Application"
    local dev_dir=/home/admin/github/tjrepo/primus
    local prod_dir=/var/www/primus

    info "Dev directory: $dev_dir"
    if [ -d "$dev_dir" ]; then
        ok "Dev directory exists"
        if [ -f "$dev_dir/manage.py" ]; then
            ok "  manage.py present"
        fi
    else
        fail "Dev directory missing"
    fi

    info "Production directory: $prod_dir"
    if [ -d "$prod_dir" ]; then
        ok "Production directory exists"
    else
        warn "Production directory not deployed"
    fi
}

check_endpoints() {
    header "HTTP Endpoints"
    for endpoint in "http://localhost/primus/" "http://localhost/tjai/"; do
        if curl -s -o /dev/null -w "%{http_code}" "$endpoint" 2>/dev/null | grep -qE "^(200|301|302)"; then
            ok "$endpoint responding"
        else
            warn "$endpoint not responding"
        fi
    done
}

# Main
echo "=============================================="
echo "  etaverse.com Server Status Report"
echo "  $(date)"
echo "=============================================="

case "$section" in
    python)  check_python ;;
    apache)  check_apache ;;
    postgres) check_postgres ;;
    deploy)  check_deploy ;;
    tjai)    check_tjai ;;
    primus)  check_primus ;;
    endpoints) check_endpoints ;;
    all)
        check_python
        check_apache
        check_postgres
        check_deploy
        check_tjai
        check_primus
        check_endpoints
        ;;
    *)
        echo "Usage: $0 [all|python|apache|postgres|deploy|tjai|primus|endpoints]"
        exit 1
        ;;
esac

echo ""
