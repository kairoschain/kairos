// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Paranoid Zero.
// Modified by the Kairos Chain contributors (rebrand to Kairos Chain).

fn main() {
    println!("cargo:rerun-if-changed=assets/app-icons/Kairos.ico");

    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }

    let manifest_directory =
        std::path::PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let icon = manifest_directory.join("assets/app-icons/Kairos.ico");
    let version = std::env::var("CARGO_PKG_VERSION").unwrap();

    let mut resource = winresource::WindowsResource::new();
    resource
        .set_icon(icon.to_str().expect("Windows icon path is UTF-8"))
        .set("FileDescription", "Kairos Wallet")
        .set("ProductName", "Kairos")
        .set("ProductVersion", &version)
        .set("FileVersion", &version)
        .set("InternalName", "Kairos")
        .set("OriginalFilename", "kairos-gui.exe")
        .set("LegalCopyright", "Copyright © 2026 Paranoid Zero")
        .set_manifest(
            r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
    </windowsSettings>
  </application>
</assembly>"#,
        );
    resource
        .compile()
        .expect("compile Kairos Windows resources");
}
