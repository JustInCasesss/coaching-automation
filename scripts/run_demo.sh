#!/usr/bin/env sh
set -eu
export COACH_DEMO_MODE=true
python -m coaching_bot.cli demo --reseed
python -m coaching_bot.cli reminders --dry-run --force-quiet
python -m coaching_bot.cli stats
printf '\nOpen out/demo_transcript.html to inspect the generated transcript.\n'
