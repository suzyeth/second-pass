# Load credentials into THIS process only, then run whatever was asked.
#
#   .\run.ps1 python agent.py "which stretches lose people?"
#
# Two sources, neither of which is a file on disk:
#   - ClickHouse host/password from the user registry (set once with setx)
#   - the Gemini key from Secret Manager, piped straight into the environment
#
# Nothing is echoed. A key that gets printed is a key that ends up in a
# screen recording, and this project is going to be screen-recorded.

$reg = Get-ItemProperty -Path 'HKCU:\Environment'
$env:CLICKHOUSE_HOST     = $reg.CLICKHOUSE_HOST
$env:CLICKHOUSE_PASSWORD = $reg.CLICKHOUSE_PASSWORD
$env:CLICKHOUSE_PORT     = '8443'
$env:CLICKHOUSE_USER     = 'default'
$env:CLICKHOUSE_SECURE   = 'true'

# The GCP project is not hard-coded here: this file is public, and the project
# also hosts an unrelated entry whose name has no business being in this repo.
# Set GCP_PROJECT, or just export GOOGLE_API_KEY yourself and skip Secret Manager.
if (-not $env:GOOGLE_API_KEY -and $env:GCP_PROJECT) {
    $env:GOOGLE_API_KEY = (gcloud secrets versions access latest `
        --secret=gemini-api-key --project=$env:GCP_PROJECT 2>$null)
}
# ADK reads GOOGLE_API_KEY; it must also be told not to route via Vertex.
$env:GOOGLE_GENAI_USE_VERTEXAI = 'FALSE'
# score-film.js reads GEMINI_API_KEY — same key, second name, so the scorer
# works through this wrapper too instead of silently seeing no credential.
$env:GEMINI_API_KEY = $env:GOOGLE_API_KEY

if (-not $env:CLICKHOUSE_HOST)  { Write-Error 'CLICKHOUSE_HOST not in registry'; exit 1 }
if (-not $env:GOOGLE_API_KEY)   { Write-Error 'set GOOGLE_API_KEY, or set GCP_PROJECT to pull it from Secret Manager'; exit 1 }

& $args[0] @($args[1..($args.Count-1)])
