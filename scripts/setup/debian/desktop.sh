#!/usr/bin/env bash

apt install i3 fuse nextcloud-desktop gnupg fonts-noto-color-emoji

url="https://discord.com/api/download?platform=linux&format=deb"
curl -L -o /tmp/discord.deb $url
apt install /tmp/discord.deb
rm -f /tmp/discord.deb

# https://support.mozilla.org/en-US/kb/install-firefox-linux#w_install-firefox-deb-package-for-debian-based-distributions-recommended
install -d -m 0755 /etc/apt/keyrings
wget -q https://packages.mozilla.org/apt/repo-signing-key.gpg -O- | sudo tee /etc/apt/keyrings/packages.mozilla.org.asc > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" | tee -a /etc/apt/sources.list.d/mozilla.list > /dev/null
echo '
Package: *
Pin: origin packages.mozilla.org
Pin-Priority: 1000
' | tee /etc/apt/preferences.d/mozilla
apt-get update && apt-get install firefox


url="https://code.visualstudio.com/sha/download?build=stable&os=linux-deb-x64"
curl -L -o /tmp/vscode.deb $url
apt install /tmp/vscode.deb
rm -f /tmp/vscode.deb
