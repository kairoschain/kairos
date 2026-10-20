// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Paranoid Zero.
// Modified by the Kairos Chain contributors (rebrand to Kairos Chain).

use iced::window;

const APPLICATION_ICON: &[u8] = include_bytes!("../assets/app-icons/Kairos-64.png");

pub fn icon() -> window::Icon {
    let icon = image::load_from_memory(APPLICATION_ICON)
        .expect("embedded application icon is valid")
        .into_rgba8();
    let (width, height) = icon.dimensions();
    window::icon::from_rgba(icon.into_raw(), width, height)
        .expect("embedded application icon dimensions are valid")
}
