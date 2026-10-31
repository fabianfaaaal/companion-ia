# Companion IA - Windows engine preparation. No administrator required by this script.
[CmdletBinding()]
param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http

function Get-OllamaCommand {
    $command = Get-Command ollama -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    return $null
}

function Test-OllamaServer {
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(3)
    $client.MaxResponseContentBufferSize = 1048576
    try {
        $response = $client.GetAsync('http://127.0.0.1:11434/api/version').GetAwaiter().GetResult()
        try {
            if (-not $response.IsSuccessStatusCode) { return $false }
            $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
            return ($body.version -is [string] -and $body.version.Length -gt 0)
        } finally { $response.Dispose() }
    } catch { return $false } finally { $client.Dispose(); $handler.Dispose() }
}

function Save-OfficialInstaller([string]$Destination) {
    $uri = [Uri]'https://ollama.com/download/OllamaSetup.exe'
    $allowedHosts = @('ollama.com','www.ollama.com','github.com','objects.githubusercontent.com','release-assets.githubusercontent.com')
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromMinutes(15)
    try {
        for ($hop = 0; $hop -lt 8; $hop++) {
            if ($uri.Scheme -ne 'https' -or $uri.Host -notin $allowedHosts -or $uri.UserInfo) {
                throw 'Installer download redirected outside approved HTTPS origins.'
            }
            $response = $client.GetAsync($uri, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
            try {
                if ([int]$response.StatusCode -in @(301,302,303,307,308)) {
                    $location = $response.Headers.Location
                    if ($null -eq $location) { throw 'Missing redirect destination.' }
                    $uri = [Uri]::new($uri, $location)
                    continue
                }
                $response.EnsureSuccessStatusCode() | Out-Null
                $inputStream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
                $outputStream = [System.IO.File]::Create($Destination)
                try { $inputStream.CopyTo($outputStream) } finally { $inputStream.Dispose(); $outputStream.Dispose() }
                return
            } finally { $response.Dispose() }
        }
        throw 'Too many installer redirects.'
    } finally { $client.Dispose(); $handler.Dispose() }
}

$ollamaExe = Get-OllamaCommand
$serverReady = Test-OllamaServer
Write-Host 'Companion IA - local engine preparation'
Write-Host ('Ollama executable detected: ' + [bool]$ollamaExe)
Write-Host ('Local API accessible: ' + $serverReady)
Write-Host 'This check does not prove GPU availability, chat operation or disabled cloud features.'
if ($CheckOnly) { exit 0 }

if (-not $ollamaExe -and -not $serverReady) {
    Write-Host 'Ollama is installed separately from the source code, using its official Windows installer.'
    Write-Host 'The engine needs several GB; the language model is an additional download.'
    if ((Read-Host 'Download the official installer now? [y/N]') -ne 'y') { exit 0 }
    $installer = Join-Path ([System.IO.Path]::GetTempPath()) ('companion-ollama-' + [guid]::NewGuid().ToString('N') + '.exe')
    try {
        Save-OfficialInstaller $installer
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate) {
            throw 'Installer signature is not valid. Nothing was executed.'
        }
        Write-Host ('Official download URL: https://ollama.com/download/OllamaSetup.exe')
        Write-Host ('Signed publisher: ' + $signature.SignerCertificate.Subject)
        Write-Host 'A valid signature alone does not identify the vendor. Review the publisher before continuing.'
        if ((Read-Host 'Do you approve this publisher and want to open the installer? [y/N]') -ne 'y') { exit 0 }
        Start-Process -FilePath $installer -Wait
    } finally {
        if (Test-Path -LiteralPath $installer) { Remove-Item -LiteralPath $installer }
    }
    $ollamaExe = Get-OllamaCommand
    $serverReady = Test-OllamaServer
}

if (-not $serverReady -and $ollamaExe) {
    Write-Host 'The executable exists, but the API is not accessible. No reinstall will be attempted.'
    Write-Host 'Open Ollama from the Start menu, then run this preparer again.'
    exit 2
}
if (-not $serverReady) { throw 'Ollama installation/API could not be verified. Open the official app and retry.' }
if (-not $ollamaExe) {
    Write-Host 'The API exists but the CLI was not located. Existing installation left unchanged.'
    exit 2
}

Write-Host 'No existing Ollama settings were changed.'
Write-Host 'For local-only mode see docs/setup-windows.md. Downloading a local model does not disable cloud features globally.'
Write-Host 'Optional starter model: qwen2.5:7b (about 4.7 GB; Apache-2.0 in its official model card).'
Write-Host 'Skip this download if you already have a suitable local model.'
if ((Read-Host 'Download qwen2.5:7b now? [y/N]') -eq 'y') {
    $previousHost = $env:OLLAMA_HOST
    try {
        $env:OLLAMA_HOST = '127.0.0.1:11434'
        & $ollamaExe pull qwen2.5:7b
        if ($LASTEXITCODE -ne 0) { throw 'Model download did not complete. It can be retried.' }
    } finally { $env:OLLAMA_HOST = $previousHost }
}
Write-Host 'Preparation finished. Companion desktop/chat integration is still in development.'
