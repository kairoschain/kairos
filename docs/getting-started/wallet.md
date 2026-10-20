# Install and use the wallet

The native GUI package contains the Kairos wallet and its private full
node. The application starts, monitors and stops that node itself. Ordinary
wallet use does not require a terminal.

## Choose the package

Download the GUI wallet for the computer from the
[GitHub release page](https://github.com/proof-native/parano1d/releases):

| Platform | Package |
|---|---|
| Debian or Ubuntu, x86-64 | `kairos-gui-vVERSION-linux-x86_64.deb` |
| Debian or Ubuntu, ARM64 | `kairos-gui-vVERSION-linux-aarch64.deb` |
| Windows x86-64 | `kairos-gui-vVERSION-windows-x86_64-setup.exe` |
| macOS Apple Silicon | `kairos-gui-vVERSION-macos-aarch64.dmg` |
| macOS Intel | `kairos-gui-vVERSION-macos-x86_64.dmg` |

Download `SHA256SUMS` from the same release. Verify the package before opening
it:

```sh
# Linux
sha256sum kairos-gui-vVERSION-linux-x86_64.deb

# macOS
shasum -a 256 kairos-gui-vVERSION-macos-aarch64.dmg
```

On Windows, use PowerShell:

```powershell
Get-FileHash .\kairos-gui-vVERSION-windows-x86_64-setup.exe -Algorithm SHA256
```

The digest must equal the matching line in `SHA256SUMS`.

## Install

### Linux

Open the `.deb` with the system Software application, or run:

```sh
sudo apt install ./kairos-gui-vVERSION-linux-x86_64.deb
```

Launch **Kairos** from the application menu.

### Windows

Run the setup program. The default install is per-user and does not require
administrator privileges.

Until releases are Authenticode-signed, SmartScreen may warn about an unknown
publisher. After verifying the SHA-256 digest, choose **More info** and
**Run anyway**.

### macOS

Open the DMG and drag **Kairos** to Applications.

Until releases are notarized with an Apple Developer ID, Control-click the
application, choose **Open**, and confirm. If macOS still blocks it, use
**System Settings → Privacy & Security → Open Anyway** after verifying the
download.

## First start

The first-run screen offers three ways to establish the 256-bit master secret:

- **Generate** creates a new random secret;
- **Import** restores an existing 64-character hexadecimal secret;
- **[Use photo](../wallet/photo-key.md)** deterministically derives the secret
  from image pixels.

Read [First run and the master secret](../wallet/first-run.md) before choosing.
The secret controls every derived address. It is not recoverable by the
project.

## Wait for the node

The top status bar shows connection and synchronization state. The wallet can
display its local data while catching up, but spendable balance and current
slot availability are final only when the node reports **ONLINE**.

The private node accepts inbound and outbound P2P traffic on TCP `9700`.
Ordinary wallet use works through outbound peers; allowing inbound TCP `9700`
also contributes connectivity to the network.

## Receive and send

The Main page shows the active address. Copy it with the adjacent copy control
and give it to the sender.

To pay:

1. open **Send** or press `F3`;
2. paste a valid `o1…` address;
3. enter the amount;
4. review selected inputs, outputs and network fee;
5. choose **Proof & Send**.

The wallet forges the private authorization locally and submits the complete
intent to its node. A successful final panel shows the logical transaction ID.
See [Send KAI](../wallet/send.md) for status and confirmation behavior.

## Remove the application

Removing the application does not remove wallet data.

On Debian or Ubuntu:

```sh
sudo apt remove kairos-gui
```

On Windows, use **Installed apps → Kairos → Uninstall**. On macOS, move the
application from Applications to Trash.

Wallet and node data remain under `.kairos` in the user profile. Remove that
directory only after exporting or backing up the master secret and any receipts
you intend to keep.

## Use contracts

Open **F7 Contracts** to create terms from a template, fund a contract, call
it or import a file from another party. See the [contract wallet guide](../contracts/gui.md).
Back up public terms and receipts alongside your master secret.
