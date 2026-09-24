// Evita janela de console no Windows em release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    edgefut_desktop_lib::run()
}
