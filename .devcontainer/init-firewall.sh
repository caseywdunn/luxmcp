#!/bin/bash
set -euo pipefail
iptables -P INPUT ACCEPT
iptables -P FORWARD ACCEPT
iptables -P OUTPUT ACCEPT
iptables -F
iptables -X
echo "Firewall: allow-all mode"
