#!/usr/bin/env sh
set -eu

python -m compileall -q coaching_bot tests
python -m unittest discover -s tests -v
printf '\nSmoke checks passed. Start the service with ADMIN_TOKEN set before deployment.\n'
