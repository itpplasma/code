REM
REM Installs essentials for local development on a Windows machine.
REM CODE can then be used either in via WSL or in the Docker devcontainer.
REM

wsl --set-default-version 2
wsl --update
wsl --install -d Debian

winget install Microsoft.WindowsTerminal
winget install Git.Git
winget install Docker.DockerDesktop
winget install Microsoft.VisualStudioCode --override "/verysilent /suppressmsgboxes /mergetasks='!runcode,addcontextmenufiles,addcontextmenufolders,associatewithfiles,addtopath'"

REM Linux line-endings needed for devcontainer to work
git config --global core.autocrlf false
git config --global core.eol lf
