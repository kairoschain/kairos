# Run a node on Linux

An ordinary Kairos node verifies complete blocks, maintains the live UTXO
State, relays transactions and serves synchronization data. It does not mine.

This guide installs the official Core release as a system service. It assumes a
64-bit Linux server with systemd.

## Requirements

The production proof backend requires:

- x86-64 with SSE4.1 and PCLMULQDQ; or
- ARM64 with NEON and PMULL.

Runtime dispatch selects the `pclmul`, `avx2+vpclmul`,
`avx512bw+vpclmul` or `neon+pmull` backend automatically. The scalar reference
backend is not used by a production node.

The node listens for P2P connections on TCP port `9700`. Its JSON-RPC endpoint
should remain bound to `127.0.0.1:9701`.

The dominant mutable storage follows the live UTXO set. Compact 212-byte
headers remain permanent, while complete block bodies are retained only for
the latest 42 blocks.

Read [Hardware and capacity](hardware.md) before ordering a virtual machine or
choosing disk and memory limits.

## Install the Core release

Download the archive for the server architecture and `SHA256SUMS` from the
[release page](https://github.com/proof-native/parano1d/releases). Verify the
archive before extracting it. Replace `VERSION` with the release number:

```sh
grep '  kairos-core-vVERSION-linux-x86_64.tar.gz$' SHA256SUMS \
  | sha256sum --check
```

The command must report `OK`. For ARM64, replace `linux-x86_64` with
`linux-aarch64`.

Extract the archive and run the hardware check:

```sh
tar -xzf kairos-core-vVERSION-linux-x86_64.tar.gz
./kairos --check-hardware
```

A supported machine ends with:

```text
NODE READY
```

Install the node and CLI:

```sh
sudo install -m 0755 kairos kairos-cli /usr/local/bin/
```

## Create the service account

Keep node data separate from interactive user accounts:

```sh
sudo useradd --system --home-dir /var/lib/kairos \
  --create-home --shell /usr/sbin/nologin kairos
sudo install -d -o kairos -g kairos -m 0700 /var/lib/kairos
sudo install -d -o root -g kairos -m 0750 /etc/kairos
```

Create `/etc/kairos/kairos.toml`:

```toml
[network]
listen = "0.0.0.0:9700"
seeds = []

[storage]
backend = "mdbx"
path = "/var/lib/kairos"

[rpc]
listen = "127.0.0.1:9701"

[mining]
enabled = false
miner_address = ""
```

Protect the configuration:

```sh
sudo chown root:kairos /etc/kairos/kairos.toml
sudo chmod 0640 /etc/kairos/kairos.toml
```

No seed address is required. The released binary discovers the public network
through its built-in DNS seeds and remembers successful outbound peers.

## Run under systemd

Create `/etc/systemd/system/kairos.service`:

```ini
[Unit]
Description=Kairos node
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=kairos
Group=kairos
ExecStart=/usr/local/bin/kairos --config /etc/kairos/kairos.toml
Restart=on-failure
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=45
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

`KillSignal=SIGINT` gives the node time to close its network services and flush
MDBX cleanly.

Load the unit and start the node:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now kairos
sudo systemctl status kairos
```

Follow startup and synchronization:

```sh
sudo journalctl -u kairos -f
```

## Check the node

The CLI connects to the local RPC endpoint by default:

```sh
kairos-cli status
kairos-cli peers
kairos-cli state
```

`status` should report the current height, `peers` should become non-zero, and
`state` reports the authenticated Live State dimensions.

## Network access

Allow inbound TCP `9700` in the host and provider firewalls. If the server is
behind NAT, forward TCP `9700` to it. A node can synchronize through outbound
connections without an inbound route, but accepting inbound peers makes it
useful to the network.

Do not expose TCP `9701` publicly. Remote administration should use an SSH
tunnel or another authenticated private transport.

## Stop or update

Stop the service before replacing binaries or copying its data:

```sh
sudo systemctl stop kairos
```

Install the verified replacement binaries, then restart:

```sh
sudo install -m 0755 kairos kairos-cli /usr/local/bin/
sudo systemctl start kairos
kairos-cli status
```

Do not remove `/var/lib/kairos` during an ordinary software update. If the
node's wallet receives funds, back up `/var/lib/kairos/wallet.key`
separately and protect it as a private secret.
