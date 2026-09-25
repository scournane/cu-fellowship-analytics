# Sets up the Slack reminders bot end to end on Windows.
#   PS> Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   PS> .\setup-reminders.ps1            # migrate + test + build
#   PS> .\setup-reminders.ps1 -Start     # ...then start the bot in Socket Mode
#   PS> .\setup-reminders.ps1 -SkipTests
param([switch]$Start, [switch]$SkipTests)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

Step "Activating virtualenv"
. .\.venv\Scripts\Activate.ps1

Step "Checking tools"
python tasks.py doctor

Step "Starting the local Supabase stack (no-op if already running)"
python tasks.py db-up

Step "Applying pending migrations to the working database (keeps existing data)"
# `supabase migration up` applies only migrations not yet recorded in the
# local schema_migrations table — unlike `db reset`, nothing is dropped.
supabase migration up
if ($LASTEXITCODE -ne 0) { throw "supabase migration up failed" }

if (-not $SkipTests) {
    Step "Creating the separate test database (cufa_test)"
    python tasks.py db-test

    Step "Running reminder / CLI / console tests"
    python -m pytest tests\test_reminders.py tests\test_cli.py tests\test_console.py -q -W ignore
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }

    Step "Building the frontend"
    Push-Location frontend
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "frontend build failed" }
    Pop-Location
}

Step "Done"
Write-Host "Bot settings live in .env (SLACK_BOT_TOKEN / SLACK_APP_TOKEN / CUFA_SLACK_* )."
Write-Host "Make sure the Slack app has chat:write + commands scopes and the /cufa-reminders"
Write-Host "slash command, then reinstall it (docs/setup/slack-bot.md#creating-the-real-slack-app)."

if ($Start) {
    Step "Starting the bot in Socket Mode (Ctrl+C to stop)"
    cufa slack socket
} else {
    Write-Host "`nStart the bot with:  cufa slack socket     (stats at http://127.0.0.1:3000/stats)"
}
