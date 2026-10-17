[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'SWLOR/tools/cleanmodels-qt/1.1.7')
)
$ErrorActionPreference = 'Stop'
$Destination = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$archive = Join-Path $Destination 'cleanmodels-qt-windows-amd64.zip'
$url = 'https://github.com/plenarius/cleanmodels-qt/releases/download/v1.1.7/cleanmodels-qt-windows-amd64.zip'
if (-not (Test-Path -LiteralPath $archive)) {
    Invoke-WebRequest -Uri $url -OutFile $archive
}
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne '9CD3595CE5B6B21C46F0FCF58D0B497941B84C2C950A3A66B432BDA134EDD28B') {
    throw "Compiler archive checksum differs from cleanmodels-qt v1.1.7: $archive"
}
Expand-Archive -LiteralPath $archive -DestinationPath $Destination -Force
$compiler = Join-Path $Destination 'cleanmodels-qt/cleanmodels.exe'
if ((Get-FileHash -LiteralPath $compiler -Algorithm SHA256).Hash -ne '7D402AF35EC5AFF6B93E7E9FDF03EDD43B91390329D9C825F008A64FDCAB8D4A') {
    throw "Bundled CleanModels executable checksum differs from the tested v4.4.2: $compiler"
}
& $compiler --version
if ($LASTEXITCODE -ne 0) { throw 'CleanModels version check failed' }
Write-Output $compiler
