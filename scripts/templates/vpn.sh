#!/bin/sh

# export BW_SESSION API token in your bashrc or zshrc

SERVER=vpn.tugraz.at
USER=<YOUR_USER>
ITEM=<YOUR_ITEM_ID>
PASS=`bw get item $ITEM | jq -r '.fields[0] .value'`
TOTP=`bw get totp $ITEM`

printf "$PASS\n$TOTP\n" | sudo openconnect -u $USER --passwd-on-stdin $SERVER
