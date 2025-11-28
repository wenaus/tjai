#!/usr/bin/env bash
# Create Django superuser for tjai admin
# Uses same credentials as primus for consistency
# Run as: ./setup_superuser.sh [username] [email]
set -euo pipefail

USERNAME=${1:-admin_regina}
EMAIL=${2:-wenaus@gmail.com}
PROD_DIR=/var/www/tjai

if [[ ! -d "$PROD_DIR/.venv" ]]; then
    echo "Error: tjai not deployed. Run update_from_dev.sh first."
    exit 1
fi

# Generate a password
PASSWORD="tjai_$(date +%Y)_$(openssl rand -hex 4)"

cd "$PROD_DIR"
.venv/bin/python -c "
import os, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'tjai_project.settings'
sys.path.insert(0, '$PROD_DIR')
import django
django.setup()
from django.contrib.auth.models import User
if User.objects.filter(username='$USERNAME').exists():
    print('User $USERNAME already exists')
else:
    User.objects.create_superuser('$USERNAME', '$EMAIL', '$PASSWORD')
    print('Superuser created:')
    print('  Username: $USERNAME')
    print('  Password: $PASSWORD')
    print('')
    print('Login at: https://etaverse.com/tjai/admin/')
"
